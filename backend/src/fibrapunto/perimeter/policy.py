"""The authorization perimeter, loaded from contracts/perimeter-policy.yaml (§8).

Two layers enforce the perimeter, in this order:

  1. This module, in Python. Checked first so the denial is *readable* —
     "svc_agent may not read crm.customer.email" rather than "permission denied
     for table customer". §8.1 records that an opaque authorization error pushes
     the engineer to widen permissions until it goes away; this layer exists so
     that pressure never arises.

  2. Postgres column grants. Checked second, and it is the authority. If this
     module is wrong, or bypassed, or deleted, the database still says no.

Layer 1 is a courtesy. Layer 2 is the perimeter. tests/test_perimeter_raw_sql.py
proves the distinction by connecting as the agent role with no Python in the loop.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

Operation = Literal["read", "create", "update", "delete"]

POLICY_PATH = Path(__file__).resolve().parents[4] / "contracts" / "perimeter-policy.yaml"


class PerimeterError(Exception):
    """Base class for every authorization failure. Never reaches the model as
    itself — capabilities/runtime.py maps it to a typed outcome (§5.1)."""


@dataclass(frozen=True, slots=True)
class OperationNotAuthorized(PerimeterError):
    principal: str
    entity: str
    operation: Operation

    def __str__(self) -> str:  # pragma: no cover - trivial
        return (
            f"{self.principal} may not {self.operation} {self.entity}. "
            f"Not granted in contracts/perimeter-policy.yaml."
        )


@dataclass(frozen=True, slots=True)
class FieldNotAuthorized(PerimeterError):
    principal: str
    entity: str
    field: str
    operation: Operation
    enforced_by: Literal["policy", "postgres"] = "policy"
    sqlstate: str | None = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        where = (
            f"postgres (SQLSTATE {self.sqlstate})"
            if self.enforced_by == "postgres"
            else "contracts/perimeter-policy.yaml"
        )
        return (
            f"{self.principal} may not {self.operation} "
            f"{self.entity}.{self.field}. Denied by {where}."
        )


@dataclass(frozen=True, slots=True)
class EntityPolicy:
    name: str
    table: str
    read: tuple[str, ...] | Literal["*"]
    denied_read: tuple[str, ...]
    create: tuple[str, ...] | Literal["*"]
    update: tuple[str, ...] | Literal["*"]
    delete: tuple[str, ...] | Literal["*"]

    def columns_for(self, operation: Operation) -> tuple[str, ...] | Literal["*"]:
        return getattr(self, operation)


@dataclass(frozen=True, slots=True)
class SequenceGrant:
    name: str
    grant: str


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is acting. Passed to every gateway call; there is no default."""

    name: str
    db_role: str
    description: str
    entities: dict[str, EntityPolicy]
    sequences: tuple[SequenceGrant, ...]
    # schema name -> granted privileges. An empty tuple means explicitly denied,
    # which is how the agent is kept out of the platform schema.
    schemas: dict[str, tuple[str, ...]]
    owns_schemas: tuple[str, ...]
    # Table privileges with no column-level form (TRUNCATE, REFERENCES...).
    # Kept separate from `entities` because they are not part of §8's
    # entity-by-verb matrix and must not be confused with read access.
    extra_table_privileges: tuple[str, ...]

    def check(
        self, entity: str, operation: Operation, fields: tuple[str, ...] = ()
    ) -> tuple[str, ...]:
        """Raise if this principal may not perform `operation` on `entity`.

        Returns the granted columns so a caller can project explicitly — with
        column grants in force, `SELECT *` on a restricted table is denied
        outright, so the gateway must never emit one.
        """
        policy = self.entities.get(entity)
        if policy is None:
            raise OperationNotAuthorized(self.name, entity, operation)

        granted = policy.columns_for(operation)
        if granted != "*" and not granted:
            raise OperationNotAuthorized(self.name, entity, operation)

        if granted == "*":
            if operation == "read" and policy.denied_read:
                for field in fields:
                    if field in policy.denied_read:
                        raise FieldNotAuthorized(self.name, entity, field, operation)
            return fields

        for field in fields:
            if field not in granted:
                raise FieldNotAuthorized(self.name, entity, field, operation)

        return fields or tuple(granted)

    def may(self, entity: str, operation: Operation, field: str | None = None) -> bool:
        """Non-raising form, for the UI's perimeter panel."""
        try:
            self.check(entity, operation, (field,) if field else ())
        except PerimeterError:
            return False
        return True


def _as_columns(value: object) -> tuple[str, ...] | Literal["*"]:
    if value == "*":
        return "*"
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(v) for v in value)
    raise ValueError(f"Expected a list or '*', got {value!r}")


@functools.cache
def load_policy(path: Path | None = None) -> dict[str, Principal]:
    """Parse the policy file into Principals. Cached: the file is a build-time
    artefact, not runtime configuration."""
    raw = yaml.safe_load((path or POLICY_PATH).read_text(encoding="utf-8"))

    principals: dict[str, Principal] = {}
    for name, spec in raw["principals"].items():
        entities = {
            entity_name: EntityPolicy(
                name=entity_name,
                table=entity_spec["table"],
                read=_as_columns(entity_spec.get("read")),
                denied_read=tuple(entity_spec.get("denied_read", ())),
                create=_as_columns(entity_spec.get("create")),
                update=_as_columns(entity_spec.get("update")),
                delete=_as_columns(entity_spec.get("delete")),
            )
            for entity_name, entity_spec in spec.get("entities", {}).items()
        }
        schemas: dict[str, tuple[str, ...]] = {}
        for schema_name, privileges in (spec.get("schemas") or {}).items():
            if privileges in (None, "none"):
                schemas[schema_name] = ()
            elif isinstance(privileges, list):
                schemas[schema_name] = tuple(str(p).upper() for p in privileges)
            else:
                raise ValueError(
                    f"{name}.schemas.{schema_name}: expected a list or 'none', got {privileges!r}"
                )

        principals[name] = Principal(
            name=name,
            db_role=spec["db_role"],
            description=spec.get("description", "").strip(),
            entities=entities,
            sequences=tuple(
                SequenceGrant(name=s["name"], grant=s["grant"]) for s in spec.get("sequences", ())
            ),
            schemas=schemas,
            owns_schemas=tuple(spec.get("owns_schemas", ())),
            extra_table_privileges=tuple(
                str(p).upper() for p in spec.get("extra_table_privileges", ())
            ),
        )
    return principals


def get_principal(name: str, path: Path | None = None) -> Principal:
    try:
        return load_policy(path)[name]
    except KeyError:
        raise OperationNotAuthorized(name, "<unknown>", "read") from None


# The two identities of §8. Import these; do not construct Principals ad hoc.
def svc_agent() -> Principal:
    return get_principal("svc_agent")


def platform_admin() -> Principal:
    return get_principal("platform_admin")

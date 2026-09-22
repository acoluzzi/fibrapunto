"""Render contracts/perimeter-policy.yaml into SQL grants (§8, §17.3).

The generated file is committed. tests/test_grants_drift.py fails if it does not
match a fresh render, so the policy file and what the database actually enforces
cannot drift apart silently.

    uv run fibrapunto-render-grants          # writes db/migrations/004_grants.generated.sql
    uv run fibrapunto-render-grants --check  # exits 1 if the committed file is stale
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fibrapunto.perimeter.policy import POLICY_PATH, EntityPolicy, Principal, load_policy

OUTPUT_PATH = Path(__file__).resolve().parents[4] / "db" / "migrations" / "004_grants.generated.sql"

HEADER = """\
-- 004_grants.generated.sql
--
-- GENERATED FILE — DO NOT EDIT.
-- Source: contracts/perimeter-policy.yaml
-- Regenerate: uv run fibrapunto-render-grants
--
-- This is §8's table as the database actually enforces it. Every column the
-- agent can read is listed here; every column that is not listed is denied by
-- the engine, not by an instruction (§17.2, §17.3).
"""

# Operations that map onto a SQL privilege. `read` becomes SELECT and so on.
SQL_PRIVILEGE = {"read": "SELECT", "create": "INSERT", "update": "UPDATE", "delete": "DELETE"}

# Tables the policy may grant on. A policy naming anything else is a mistake we
# want loud rather than silently ungranted.
KNOWN_TABLES = {
    "crm.customer",
    "crm.contract",
    "crm.handover_record",
    "crm.v_handover_completeness",
}


def _entity_grants(role: str, entity: EntityPolicy) -> list[str]:
    lines: list[str] = []

    for operation, privilege in SQL_PRIVILEGE.items():
        columns = entity.columns_for(operation)  # type: ignore[arg-type]

        if columns == "*":
            lines.append(f"GRANT {privilege} ON {entity.table} TO {role};")
            continue

        if not columns:
            continue

        # DELETE has no column-level form in Postgres; a column list there is a
        # policy authoring error, not something to silently widen.
        if privilege == "DELETE":
            raise ValueError(
                f"{entity.name}: DELETE cannot be column-scoped. "
                f"Use '*' to grant it or [] to deny it."
            )

        column_list = ", ".join(columns)
        lines.append(f"GRANT {privilege} ({column_list}) ON {entity.table} TO {role};")

    return lines


def _principal_block(principal: Principal) -> list[str]:
    role = principal.db_role
    lines = [
        "",
        "-- " + "-" * 74,
        f"-- {principal.name}  (db role: {role})",
    ]
    for wrapped in principal.description.splitlines():
        if wrapped.strip():
            lines.append(f"-- {wrapped.strip()}")
    lines += ["-- " + "-" * 74, ""]

    # Start from nothing, every time. A grant this principal holds is one that
    # appears below, not one that survived from an earlier migration.
    all_schemas = ", ".join(sorted(principal.schemas)) or "crm"
    lines.append(f"REVOKE ALL ON ALL TABLES IN SCHEMA {all_schemas} FROM {role};")
    for schema_name in sorted(principal.schemas):
        lines.append(f"REVOKE ALL ON SCHEMA {schema_name} FROM {role};")
    lines.append("")

    for schema_name, privileges in sorted(principal.schemas.items()):
        if not privileges:
            lines.append(f"-- schema {schema_name}: no access, deliberately")
            continue
        lines.append(f"GRANT {', '.join(privileges)} ON SCHEMA {schema_name} TO {role};")
    lines.append("")

    # A schema this principal owns is granted wholesale — there is no boundary
    # to draw inside it, and enumerating it would rot on the next migration.
    for schema_name in principal.owns_schemas:
        lines += [
            f"GRANT ALL ON ALL TABLES    IN SCHEMA {schema_name} TO {role};",
            f"GRANT ALL ON ALL SEQUENCES IN SCHEMA {schema_name} TO {role};",
            "",
        ]

    for entity in principal.entities.values():
        if entity.table not in KNOWN_TABLES:
            raise ValueError(f"Unknown table in policy: {entity.table}")

        denied = ""
        if entity.denied_read:
            denied = f"   -- denied: {', '.join(entity.denied_read)}"
        lines.append(f"-- {entity.name}{denied}")
        lines += _entity_grants(role, entity)
        for privilege in principal.extra_table_privileges:
            lines.append(f"GRANT {privilege} ON {entity.table} TO {role};")
        lines.append("")

    for sequence in principal.sequences:
        lines.append(f"GRANT {sequence.grant} ON SEQUENCE {sequence.name} TO {role};")
    if principal.sequences:
        lines.append("")

    return lines


def render(policy_path: Path | None = None) -> str:
    principals = load_policy(policy_path or POLICY_PATH)
    lines = [HEADER]
    for principal in principals.values():
        lines += _principal_block(principal)
    lines.append(
        "-- Everything not granted above is denied. In particular the agent has no\n"
        "-- grant on schema platform, so it cannot read its own transcripts.\n"
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the committed file differs from a fresh render",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()

    rendered = render()

    if args.check:
        if not args.output.exists():
            print(f"{args.output} does not exist. Run without --check.", file=sys.stderr)
            return 1
        if args.output.read_text(encoding="utf-8") != rendered:
            print(
                f"{args.output} is stale.\n"
                f"contracts/perimeter-policy.yaml has changed since it was generated.\n"
                f"Run: uv run fibrapunto-render-grants",
                file=sys.stderr,
            )
            return 1
        print(f"{args.output.name} is up to date.")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

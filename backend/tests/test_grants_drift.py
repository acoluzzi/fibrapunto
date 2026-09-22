"""The committed grants match the perimeter policy (§8, §17.3).

contracts/perimeter-policy.yaml is the source of truth for §8's table.
db/migrations/004_grants.generated.sql is what the database actually enforces.
If those two drift apart, the policy file becomes documentation — and §8 is
explicit that a guardrail which lives only in documentation is not a guardrail.

These tests are the gate. They are cheap and they run on every commit.
"""

from __future__ import annotations

from fibrapunto.perimeter.policy import load_policy
from fibrapunto.perimeter.render_grants import OUTPUT_PATH, render


def test_committed_grants_are_not_stale():
    assert OUTPUT_PATH.exists(), "Run: uv run fibrapunto-render-grants"
    assert OUTPUT_PATH.read_text(encoding="utf-8") == render(), (
        "db/migrations/004_grants.generated.sql does not match "
        "contracts/perimeter-policy.yaml.\n"
        "Run: uv run fibrapunto-render-grants"
    )


def test_agent_is_never_granted_the_denied_columns():
    """The columns §8 excludes must not appear in any GRANT to the agent role.

    A belt-and-braces check on the renderer itself: the raw-SQL tests prove the
    database denies them, this proves we never even asked.
    """
    sql = OUTPUT_PATH.read_text(encoding="utf-8")
    agent_grants = [
        line for line in sql.splitlines() if line.startswith("GRANT") and "fibrapunto_agent" in line
    ]
    assert agent_grants, "Expected some grants to the agent role"

    for line in agent_grants:
        if "crm.customer" in line:
            assert "email" not in line, f"Agent granted email: {line}"
            assert "phone" not in line, f"Agent granted phone: {line}"
        if "crm.handover_record" in line:
            assert "internal_comments" not in line, f"Agent granted internal_comments: {line}"


def test_agent_has_no_write_access_to_contract_or_customer():
    """§8: no writes. Contract state is a human's decision."""
    sql = OUTPUT_PATH.read_text(encoding="utf-8")
    for line in sql.splitlines():
        if "fibrapunto_agent" not in line or not line.startswith("GRANT"):
            continue
        if "crm.contract" in line or "crm.customer" in line:
            assert line.startswith("GRANT SELECT"), f"Unexpected write grant: {line}"


def test_agent_cannot_update_or_delete_a_handover_record():
    """§8: "Does not edit or close it: once open, it belongs to people."""
    sql = OUTPUT_PATH.read_text(encoding="utf-8")
    for line in sql.splitlines():
        if "fibrapunto_agent" in line and "crm.handover_record" in line:
            assert not line.startswith("GRANT UPDATE"), line
            assert not line.startswith("GRANT DELETE"), line


def test_agent_is_denied_the_platform_schema():
    """The agent cannot read its own transcripts — same decision as the email."""
    agent = load_policy()["svc_agent"]
    assert agent.schemas.get("platform") == (), (
        "The agent must have no privileges on schema platform"
    )
    assert "platform" not in agent.owns_schemas


def test_every_entity_the_agent_can_reach_is_in_the_crm_schema():
    agent = load_policy()["svc_agent"]
    for entity in agent.entities.values():
        assert entity.table.startswith("crm."), entity.table

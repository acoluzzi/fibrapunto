"""What a run cost (§16).

    "Conversational agents are priced per conversation, one way or another. [...]
    the autonomous-resolution metric is also the cost metric."

§16 argues about cost and the build had no way to measure it: token usage is not
recorded anywhere on the agent path, so "a resolved billing conversation costs a
fraction of an operator's minutes" was an assertion with no number behind it.

This measures it from **outside**, by reading the provider's own balance before
and after — the same stance §14.2 takes towards the agent. The alternative was
summing `usage` fields from model responses, which would have meant touching the
reasoning path after the day-7 freeze, and would have measured what we *think*
we were billed rather than what we were.

The number is an upper bound on the run and a lower bound on nothing: it
includes the router, the agent, the judge, and any retry the provider charged
for. That is the honest figure to divide by the case count.
"""

from __future__ import annotations

import os

import httpx

CREDITS_URL = "https://openrouter.ai/api/v1/credits"


async def balance() -> float | None:
    """Remaining credit, or None if it cannot be read.

    Never raises. A run that produced real verdicts must not be discarded
    because an accounting endpoint was down — the cost figure is useful, the
    measurement is the point.
    """
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(CREDITS_URL, headers={"Authorization": f"Bearer {key}"})
            response.raise_for_status()
            data = response.json()["data"]
        return float(data["total_credits"]) - float(data["total_usage"])
    except Exception:  # noqa: BLE001
        return None


def report(before: float | None, after: float | None, cases: int) -> dict[str, float | None]:
    """The three numbers worth committing: what it cost, what remains, and what
    one case costs — the last being the one that answers "can I afford to tune
    against this harness?"."""
    if before is None or after is None:
        return {"spent": None, "remaining": after, "per_case": None}
    spent = round(before - after, 4)
    return {
        "spent": spent,
        "remaining": round(after, 4),
        "per_case": round(spent / cases, 4) if cases else None,
    }


def format_report(spend: dict[str, float | None], cases: int) -> str:
    if spend["spent"] is None:
        return "cost: not measured (no key, or the endpoint was unreachable)"
    runs_left = ""
    if spend["remaining"] is not None and spend["spent"]:
        affordable = int(spend["remaining"] // spend["spent"])
        runs_left = f", enough for ~{affordable} more runs"
    return (
        f"cost: ${spend['spent']:.4f} for {cases} cases "
        f"(${spend['per_case']:.4f}/case) — ${spend['remaining']:.2f} remaining{runs_left}"
    )

"""Drive sessions over HTTP (§14.2).

    "An external program, outside the agent's platform. For each case it opens a
    session through the agent's programmatic interface, sends the turns and
    collects the replies."

Deliberately no import of the backend package — `evals/pyproject.toml` does not
depend on it. Importing `Conversation` in-process would be easier and would mean
§17.8's programmatic interface never gets exercised by the thing it exists for.

The stub's failure mode rides on session creation, so cases run concurrently
without colliding (§7).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import httpx


@dataclass
class Transcript:
    case_id: str
    conversation_id: str
    replies: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    error: str | None = None


class Driver:
    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 120.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def run_case(self, case: dict[str, Any], client: httpx.AsyncClient) -> Transcript:
        mode = case.get("billing_stub_mode", "none")
        try:
            # The anonymous browser identity every session route requires. The
            # client's cookie jar carries it for the rest of the case.
            (await client.post(f"{self._base}/api/customer/access")).raise_for_status()
            created = await client.post(
                f"{self._base}/api/sessions",
                json={"test_mode": None if mode == "none" else mode},
            )
            created.raise_for_status()
            conversation_id = created.json()["conversation_id"]
        except httpx.HTTPError as exc:
            return Transcript(case["id"], "", [], [], error=f"session create failed: {exc}")

        replies: list[dict[str, Any]] = []
        for turn in case["turns"]:
            try:
                response = await client.post(
                    f"{self._base}/api/sessions/{conversation_id}/turns",
                    # One id per message: a retry of this request would get
                    # the stored reply instead of a second model call.
                    json={"client_turn_id": str(uuid4()), "text": turn["user"]},
                )
                response.raise_for_status()
                replies.append(response.json())
            except httpx.HTTPError as exc:
                return Transcript(
                    case["id"], conversation_id, replies, [], error=f"turn failed: {exc}"
                )

        trace: list[dict[str, Any]] = []
        try:
            fetched = await client.get(f"{self._base}/api/sessions/{conversation_id}/trace")
            if fetched.status_code == 200:
                trace = fetched.json()
        except httpx.HTTPError:
            pass  # the trace is evidence, not a dependency

        return Transcript(case["id"], conversation_id, replies, trace)

    async def run_all(self, cases: list[dict[str, Any]], concurrency: int = 2) -> list[Transcript]:
        """Run cases with bounded concurrency.

        The default is deliberately low: a provider's in-flight reservation
        ceiling is the binding constraint here, not our throughput
        (docs/FRICTION-LOG.md 5). Raise it once credits are purchased.
        """
        semaphore = asyncio.Semaphore(concurrency)

        async with httpx.AsyncClient(timeout=self._timeout) as client:

            async def guarded(case):
                async with semaphore:
                    return await self.run_case(case, client)

            return await asyncio.gather(*(guarded(case) for case in cases))

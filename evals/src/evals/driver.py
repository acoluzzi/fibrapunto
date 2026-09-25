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

# A provider rate limit is per minute; three waits of 20, 40 and 60 seconds
# outlast one comfortably without turning a real outage into a long hang.
TURN_ATTEMPTS = 4
RETRY_AFTER_SECONDS = 20


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
                response = await self._send_turn(client, conversation_id, turn["user"])
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

    async def _send_turn(
        self, client: httpx.AsyncClient, conversation_id: str, text: str
    ) -> httpx.Response:
        """One customer turn, retried when the reasoning layer is unavailable.

        A 503 is the backend saying, honestly, that the model provider refused
        the call — in a whole run of 32 cases that is the provider's
        per-minute rate limit, not a fault of the agent (§17.10 reports it; it
        does not paper over it). The harness is the place to wait and try
        again, and the interface makes that safe: the same `client_turn_id`
        is sent every time, so a turn that did get through returns its stored
        reply instead of running the model twice.
        """
        client_turn_id = str(uuid4())
        for attempt in range(TURN_ATTEMPTS):
            response = await client.post(
                f"{self._base}/api/sessions/{conversation_id}/turns",
                json={"client_turn_id": client_turn_id, "text": text},
            )
            if response.status_code != 503 or attempt == TURN_ATTEMPTS - 1:
                response.raise_for_status()
                return response
            await asyncio.sleep(RETRY_AFTER_SECONDS * (attempt + 1))
        raise AssertionError("unreachable")  # pragma: no cover

    async def run_all(self, cases: list[dict[str, Any]], concurrency: int = 2) -> list[Transcript]:
        """Run cases with bounded concurrency.

        The default is deliberately low: a provider's in-flight reservation
        ceiling is the binding constraint here, not our throughput
        (docs/FRICTION-LOG.md 5). Raise it once credits are purchased.
        """
        semaphore = asyncio.Semaphore(concurrency)

        # One client — one cookie jar — per case. Every session route checks
        # that the browser identity in the cookie owns the conversation, and a
        # jar shared across concurrent cases is a race: case A creates its
        # session under identity 1, case B's access call then overwrites the
        # jar with identity 2, and A's next turn is answered 404 (by design —
        # the backend must not confirm another browser's conversation exists).
        # Each case is its own browser, exactly as it would be in production.
        async def guarded(case):
            async with semaphore:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    return await self.run_case(case, client)

        return await asyncio.gather(*(guarded(case) for case in cases))

"""Failure injection (§7).

"The stub exists so that failures are provoked on purpose and repeated on
command, during evaluation and during the live demo. An integration that has
only ever been seen working has not been tested; it has been observed."

Two injection paths, deliberately:

  * the `X-Test-Mode` request header — session-scoped, which is what lets the
    eval harness run 40 cases concurrently without them stepping on each other.
    This is the one that matters for §14.

  * `POST /_control/mode` with a TTL — a global override, so a failure can be
    provoked mid-conversation from a button during a live demo without
    restarting anything or touching a terminal.

The header wins over the global override, so a harness run is never disturbed
by a mode someone left set.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from enum import StrEnum

from mock_billing.settings import get_settings


class TestMode(StrEnum):
    normal = "normal"
    timeout = "timeout"
    slow = "slow"
    # No `notfound` or `duplicate`: those are facts about a contract, so they
    # live in the data (fixtures/billing.yaml). A mode is only for what data
    # cannot express — the system being slow or not answering at all.


@dataclass
class GlobalOverride:
    """A mode set through /_control/mode, with an expiry.

    The TTL matters: a demo where someone forgets to switch `timeout` back off
    produces a confusing second act. It expires on its own.
    """

    mode: TestMode = TestMode.normal
    expires_at: float = 0.0

    def current(self) -> TestMode:
        if self.mode is TestMode.normal or time.monotonic() >= self.expires_at:
            return TestMode.normal
        return self.mode

    def set(self, mode: TestMode, ttl_seconds: float) -> None:
        self.mode = mode
        self.expires_at = time.monotonic() + ttl_seconds

    def clear(self) -> None:
        self.mode = TestMode.normal
        self.expires_at = 0.0


_override = GlobalOverride()


def global_override() -> GlobalOverride:
    return _override


def resolve_mode(header_value: str | None) -> TestMode:
    """Per-request mode. The header wins; the global override is the fallback."""
    if header_value:
        try:
            return TestMode(header_value.strip().lower())
        except ValueError:
            # An unknown mode is a caller error, not a licence to behave
            # unpredictably. Treat it as normal and let the response be boring.
            return TestMode.normal
    return _override.current()


async def apply_latency(mode: TestMode) -> None:
    """Behave like an old system.

    A real legacy billing platform is slow even when it is working, so the
    baseline delay applies to *every* successful response rather than only to
    the failure modes. Without it the trace panel shows a column of zeros and
    the §16 latency argument has nothing to point at.
    """
    settings = get_settings()

    match mode:
        case TestMode.timeout:
            # Longer than the client's timeout, on purpose. The client gives up
            # first with a read timeout; the eventual 504 exercises the other
            # branch. Both must map to the same typed outcome, and both are
            # tested — they are different code paths and usually only one of
            # them gets covered.
            await asyncio.sleep(settings.timeout_mode_delay_s)
        case TestMode.slow:
            # Succeeds, but visibly slowly. Sits below the client timeout so it
            # is a latency problem and not a failure.
            await asyncio.sleep(settings.slow_mode_delay_s)
        case _:
            jitter = random.uniform(-settings.latency_jitter_ms, settings.latency_jitter_ms)
            delay_ms = max(0.0, settings.base_latency_ms + jitter)
            await asyncio.sleep(delay_ms / 1000)

"""The enforcement points (§9).

`detectors.py` answers "is there an IBAN in this string". This module answers
"and therefore what", at each of the four boundaries a value could cross:

    1. inbound → model      before the turn leaves this process
    2. → stored transcript  before anything is written to disk
    3. → handover record    before a colleague reads it
    4. → customer           before it is repeated back

**Only the first is optional.** `REDACT_INBOUND` exists as a toggle because §9
states an honest limitation — "by the time that rule applies, the text has
already reached the model for that turn" — and being able to demonstrate both
behaviours is worth more than hiding one. See docs/DECISIONS.md #16.

The other three are unconditional, and that is the whole point. If storage
redaction only worked while inbound redaction was on, then flipping the demo
toggle would start writing raw IBANs to disk — the toggle would silently be a
data-protection switch rather than a model-exposure switch. §11's argument
about layers applies here exactly: each boundary defends itself, and is tested
by removing the one above it.

That is also why point 4 exists at all. With inbound redaction on, the model
never receives the digits and structurally cannot echo them; the guard looks
redundant. With the toggle off it is the only thing standing between a pasted
card number and it appearing in the reply — which is precisely when a guard
that had been left out would be missed.
"""

from __future__ import annotations

from typing import Any

import structlog

from fibrapunto.privacy import detectors
from fibrapunto.privacy.detectors import SensitiveKind
from fibrapunto.settings import get_settings

log = structlog.get_logger(__name__)


def for_model(text: str) -> tuple[str, list[SensitiveKind]]:
    """Point 1 — the only optional one (§9, DECISIONS #16).

    Returns the text as it will be sent to the provider. When redaction is on
    the model still learns that an IBAN was pasted, so it can correctly tell the
    customer not to send one — but the digits never leave this process.
    """
    if not get_settings().redact_inbound:
        return text, detectors.find_kinds(text)
    return detectors.redact(text)


def for_storage(text: str | None) -> tuple[str | None, list[str]]:
    """Point 2 — unconditional (§9).

    Applied at the moment of writing rather than trusting the caller to have
    done it. `SessionStore.commit` (persistence/sessions.py) is the only write path to
    `platform.turn` and it calls this unconditionally, because a column named
    `text_redacted` is documentation and documentation is not an enforcement
    mechanism.
    """
    if text is None:
        return None, []
    redacted, kinds = detectors.redact(text)
    return redacted, [k.value for k in kinds]


def for_handover(context: dict[str, Any]) -> dict[str, Any]:
    """Point 3 — unconditional (§9, §13).

    "Never write it into a handover record or any other stored field." The
    record is read by a colleague and lives longer than the conversation, so it
    is the worst place for a value to come to rest.

    Walks nested structures because the context block is assembled from typed
    state and will grow fields; a version that only checked the top level would
    be correct today and quietly wrong after the next addition.
    """
    return _scrub(context)


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        redacted, _ = detectors.redact(value)
        return redacted
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def for_customer(reply: str) -> tuple[str, list[SensitiveKind]]:
    """Point 4 — unconditional (§9).

    "Never repeat such a value back in the conversation." Returns the reply with
    any sensitive value removed, and what was found — a non-empty list here is
    an incident worth logging, because it means the reasoning layer produced a
    value it should never have held.
    """
    redacted, kinds = detectors.redact(reply)
    if kinds:
        log.warning(
            "privacy.echo_prevented",
            # The KIND, never the value (§9) — including in our own logs.
            kinds=[k.value for k in kinds],
        )
    return redacted, kinds

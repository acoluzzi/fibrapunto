"""The ordered guard set (§11).

    "Five layers, hardest first. A guardrail that exists at only one layer is
    fragile, and the layer it usually exists at — the prompt — is the softest
    one."

These are the output-boundary guards. They are not the whole of §11 — layer 1 is
Postgres column grants, layer 2 is the typed-capability contract — and saying so
matters, because a reader who sees only this file would conclude the guardrails
live in the place the spec says they should not.

What happens when a guard fires, in order:

  1. **Repair.** One targeted re-ask, naming the specific problem. Most
     violations are the model being helpful in a way it was not asked to be, and
     a specific instruction fixes them.
  2. **Substitute.** If the repair also fails, a hand-written Italian fallback
     replaces the reply entirely. §16 accepts model variance on *reading* the
     customer; it does not accept it on what the company says.

Every firing is recorded. §12.5's argument applies here too: a guard that fires
often is a signal about the prompt or the model, and it should be visible as a
rate rather than buried in a log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import structlog

from fibrapunto.guardrails import (
    output_grounding,
    output_language,
    output_promises,
    output_visibility,
)
from fibrapunto.state.models import ConversationState

log = structlog.get_logger(__name__)


class GuardName(StrEnum):
    grounding = "grounding"
    visibility = "visibility"
    promises = "promises"
    language = "language"


# Hardest first. Grounding and promises are the two whose §1 target is zero, so
# they are checked before the stylistic ones — if a reply is going to be thrown
# away, it should be for the reason that matters.
ORDER = (GuardName.grounding, GuardName.promises, GuardName.visibility, GuardName.language)

# A guard whose violation cannot be repaired by rewording. An invented figure
# can be removed; an invented figure the model believes is real comes back.
BLOCKING = frozenset({GuardName.grounding, GuardName.promises})


@dataclass(slots=True)
class Violation:
    guard: GuardName
    problems: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return self.guard in BLOCKING


@dataclass(slots=True)
class GuardResult:
    text: str
    violations: list[Violation] = field(default_factory=list)
    repaired: bool = False
    substituted: bool = False

    @property
    def clean(self) -> bool:
        return not self.violations


SAFE_FALLBACK = (
    "I'm sorry — I can't complete that check reliably, and I'd rather not give "
    "you information I can't confirm. If you'd like, I can put you through to "
    "a colleague who can look into it."
)


def inspect(reply: str, state: ConversationState) -> list[Violation]:
    """Run every guard. Returns what fired, in ORDER."""
    checks = {
        GuardName.grounding: lambda: [str(p) for p in output_grounding.check(reply, state)],
        GuardName.promises: lambda: output_promises.check(reply, state),
        GuardName.visibility: lambda: output_visibility.check(reply, state),
        GuardName.language: lambda: output_language.check(reply),
    }

    violations = []
    for name in ORDER:
        problems = checks[name]()
        if problems:
            violations.append(Violation(name, problems))
    return violations


def repair_instruction(violations: list[Violation]) -> str:
    """A targeted re-ask, naming what was wrong.

    Deliberately specific: "rewrite without inventing anything" produces a
    differently invented reply. "€24.90 does not come from any check you made"
    gives the model something to act on.
    """
    lines = ["The message you wrote cannot be sent to the customer."]

    for violation in violations:
        match violation.guard:
            case GuardName.grounding:
                lines.append(
                    "- It contains figures that come from no check you made: "
                    + ", ".join(violation.problems)
                    + ". You may not state them. If you have not verified "
                    "something, tell the customer so."
                )
            case GuardName.promises:
                lines.append(
                    "- It contains a commitment you cannot make: "
                    + ", ".join(violation.problems)
                    + ". You cannot issue refunds or credits, change a "
                    "contract or cancel one, and you cannot say that any of "
                    "those has happened."
                )
            case GuardName.visibility:
                lines.append(
                    "- It contains internal technical terms: "
                    + ", ".join(violation.problems)
                    + ". The customer must never see these."
                )
            case GuardName.language:
                lines.append(
                    "- Problems with wording or register: "
                    + ", ".join(violation.problems)
                    + ". Write plainly and professionally, without system "
                    "vocabulary and without over-familiarity."
                )

    lines.append("Rewrite it now, correcting only these points.")
    return "\n".join(lines)


def resolve(
    reply: str,
    state: ConversationState,
    repaired_reply: str | None = None,
) -> GuardResult:
    """Decide what the customer actually receives.

    Called twice per turn at most: once on the model's reply, and once on the
    repair if there was one. `repaired_reply` being present means this is the
    second pass, and there is no third.
    """
    candidate = repaired_reply if repaired_reply is not None else reply
    violations = inspect(candidate, state)

    if not violations:
        return GuardResult(text=candidate, repaired=repaired_reply is not None)

    for violation in violations:
        log.warning(
            "guardrail.triggered",
            guard=violation.guard.value,
            problems=violation.problems,
            second_pass=repaired_reply is not None,
            conversation_id=str(state.id),
        )

    if repaired_reply is None:
        # First failure: the caller should repair and call again.
        return GuardResult(text=candidate, violations=violations)

    # The repair failed too. Substitute rather than send it.
    if any(v.blocking for v in violations):
        log.error(
            "guardrail.substituted",
            guards=[v.guard.value for v in violations],
            conversation_id=str(state.id),
        )
        return GuardResult(
            text=SAFE_FALLBACK,
            violations=violations,
            repaired=True,
            substituted=True,
        )

    # Non-blocking only (style, register): send it and record the miss. A reply
    # with an anglicism in it is worse than one without, and better than a
    # canned refusal.
    return GuardResult(text=candidate, violations=violations, repaired=True)

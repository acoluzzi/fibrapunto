"""The reply reads like a company, not like a system (§10 criterion 3).

§10's third criterion is language quality — "handling of typos and dialect,
correct register with an older customer, and not drifting into technical
vocabulary". A reply degrades in two directions, and both are visible:

  * **Downwards into the machine.** The vocabulary of the system itself —
    `serviceUnavailable`, "handover record", "the queue", "the lookup
    returned". Every one of those is a real word in this codebase, which is
    exactly why it leaks: the model has been reading them all prompt.

  * **Sideways into familiarity.** Getting the register wrong with an older
    customer is a visible error. English has no grammatical marker for it, so
    the check is lexical: the greetings and fillers that make a utility sound
    like a chatbot.

Neither is blocking (see pipeline.BLOCKING). A reply that says "your handover
record" is worse writing, not a false statement, and the repair pass rewords it.
"""

from __future__ import annotations

import re

# The system's own vocabulary. These are the words in the tool schemas, the
# outcome literals and the queue names — the ones the model has been reading for
# three thousand tokens and will reach for if the reply gets lazy.
#
# Several near-misses are deliberately absent, and each one was a false positive
# before it was removed:
#
#   * `case` — "I've opened a case" is exactly what the agent should say.
#   * `contract`, `invoice`, `payment` — the customer's own words.
#   * "billing operations", "retention team" — these are the `queue_label`
#     values, which are marked CustomerVisible precisely so the customer is told
#     who will call them. The *internal* forms are `billing_operations` and
#     `retention`, and those are already covered by output_visibility, which
#     derives its blocklist from the outcome models rather than from a list
#     anyone has to remember to update.
_INTERNAL_VOCABULARY = frozenset(
    {
        "handover",
        "handover record",
        "capability",
        "lookup",
        "the queue",
        "queued",
        "escalate",
        "escalation",
        "serviceunavailable",
        "notfound",
        "multiplematches",
        "timeout",
        "timed out",
        "endpoint",
        "api",
        "backend",
        "system error",
        "exception",
        "incident id",
        "downstream",
    }
)

# Over-familiarity. A utility writing to someone who may be seventy does not
# open with "Hi there" or agree with "absolutely".
_TOO_FAMILIAR = re.compile(
    r"\b(?:hey|hi there|no worries|no problem|absolutely|sure thing|awesome|"
    r"gotcha|yeah|yep|super|totally|my friend|buddy)\b",
    re.IGNORECASE,
)

# Manufactured enthusiasm, which the base identity forbids outright.
_ENTHUSIASM = re.compile(r"[!]{1,}|\b(?:great news|happy to say|delighted)\b", re.IGNORECASE)


def check(reply: str) -> list[str]:
    problems: list[str] = []
    lowered = reply.lower()

    for phrase in sorted(_INTERNAL_VOCABULARY):
        if re.search(rf"\b{re.escape(phrase)}\b", lowered):
            problems.append(f"internal vocabulary: {phrase}")

    if match := _TOO_FAMILIAR.search(reply):
        problems.append(f"over-familiar register: {match.group()}")

    if match := _ENTHUSIASM.search(reply):
        problems.append(f"manufactured enthusiasm: {match.group()!r}")

    return problems

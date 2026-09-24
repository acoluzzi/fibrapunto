"""The reply is in English, whatever language the customer wrote in.

Not a style preference. Every other guard that reads the reply — grounding's
dates and amounts, the promises patterns, the commitment sentences the closing
template replaces — is written for English. A reply in another language passes
all of them unread: "ho annullato il contratto" is a cancellation claimed as
executed that the promises guard cannot see. So a reply that is not English is
blocked like one that invents a figure: one repair, then the fallback.

The detector is deliberately plain. English prose is dense with a small set of
function words — articles, pronouns, auxiliaries, prepositions — and prose in
another language almost never uses them. A reply of any length where they are
nearly absent is not English. Short replies are exempt: "Thank you." proves
nothing either way, and blocking it would be worse than letting it through.
"""

from __future__ import annotations

import re

_FUNCTION_WORDS = frozenset(
    """
    a an the and or but if so of to in on at by for with from about as into
    i you your yours we our us it its this that these those there here
    is are was were be been being am do does did have has had can could will
    would shall should may might must not no yes
    what which who when where why how
    please thank thanks sorry
    my me he she they them their his her
    all any some one more than then also just only now
    """.split()
)

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

MIN_WORDS = 5
"""Below this, the sample is too small to judge."""

MIN_SHARE = 0.15
"""English prose runs well above this share of function words; a reply in
another language sits near zero. Measured on the agent's own replies and on
the templates, which all clear it comfortably."""


def check(reply: str) -> list[str]:
    words = [w.lower() for w in _WORD.findall(reply)]
    if len(words) < MIN_WORDS:
        return []
    share = sum(w in _FUNCTION_WORDS for w in words) / len(words)
    if share < MIN_SHARE:
        return ["the reply is not in English"]
    return []

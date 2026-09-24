"""Detect payment credentials and personal data in free text (§9).

Every detector pairs a pattern with a **checksum**. Regex alone produces false
positives, and a redaction demo that mangles an ordinary number in front of an
audience discredits the whole pipeline — the one thing §9 cannot afford, since
its claims are the hardest in the document to verify by looking.

§9's rules for an unsolicited credential:

    "never repeat such a value back in the conversation; never write it into a
    handover record or any other stored field; tell the customer plainly not to
    share payment credentials in chat, and give them the secure payment route
    instead; continue the conversation without it."

The *kind* is recorded, never the value. `["IBAN"]`, not the IBAN.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class SensitiveKind(StrEnum):
    iban = "IBAN"
    card = "card number"
    cvv = "CVV"
    # Kept: the codice fiscale is an Italian identifier, and Fibrapunto is an
    # Italian operator whose customers have one. Detecting it is a real
    # requirement regardless of the language the conversation happens in.
    codice_fiscale = "codice fiscale"
    password = "password"


@dataclass(frozen=True, slots=True)
class Finding:
    kind: SensitiveKind
    start: int
    end: int

    @property
    def placeholder(self) -> str:
        return f"[[REDACTED:{self.kind.name.upper()}]]"


# --- patterns ----------------------------------------------------------------

# Where a candidate may START. Where it ends is decided by the checksum, not by
# the pattern: see `_candidates`.
#
# The previous patterns matched as far as they could and validated once. So
# "IT60X…456 thanks" became one 33-character candidate — letters are legal IBAN
# characters — failed mod-97, and went through unredacted; a card number
# followed by its expiry ("… 1111 12/27") took the "12" along and failed Luhn.
# Every test had put the value at the end of the string, where greed is
# harmless. A checksum that is only ever tried on the longest match turns a
# false-positive guard into a false-negative generator.
_IBAN_START = re.compile(r"(?<![A-Z0-9])[A-Z]{2}\d{2}", re.IGNORECASE)
_CARD_START = re.compile(r"(?<![\d-])\d")

# ISO 13616 lengths for the countries a Fibrapunto customer plausibly banks in.
# With the country known, the length is known, and the candidate ends there
# whatever follows it. Others fall back to the longest checksum-valid prefix.
_IBAN_LENGTHS = {
    "IT": 27, "SM": 27, "VA": 22, "FR": 27, "DE": 22, "ES": 24, "PT": 25,
    "GB": 22, "IE": 22, "NL": 18, "BE": 16, "LU": 20, "AT": 20, "CH": 21,
    "MC": 27, "GR": 27, "PL": 28, "RO": 24, "HR": 21, "SI": 19, "MT": 31,
}  # fmt: skip
_CVV = re.compile(
    r"(?:\bcvv2?\b|\bcv2\b|\bcvc2?\b|\bcsc\b|security (?:code|number)|verification (?:code|number)"
    r"|code on the back|(?:three|3|four|4)[ -]digits? (?:on|at) the back|digits on the back"
    r"|codice di sicurezza)\D{0,15}(\d{3,4})\b",
    re.IGNORECASE,
)
# A bare 3–4 digit number right after a card number, optionally after its
# expiry: "4111 … 1111 12/27 123". Nobody types a card, then a short number,
# for any other reason.
_CVV_AFTER_CARD = re.compile(
    r"^[\s,;:.-]*(?:(?:exp(?:iry|ires)?\.?|valid (?:to|until)|scad(?:enza)?)\s*:?\s*)?"
    r"(?:\d{1,2}\s*[/-]\s*\d{2,4}[\s,;:.-]*)?(?:(?:and\s+)?(?:cvv|cvc|code)\s*:?\s*)?"
    r"(\d{3,4})\b(?![/.-]\d)",
    re.IGNORECASE,
)
_CODICE_FISCALE = re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPR-T]\d{2}[A-Z]\d{3}[A-Z]\b", re.IGNORECASE)
_PASSWORD = re.compile(
    r"\b(?:password|pass\s?word|passcode|passphrase|pwd|pin|login|access code)"
    r"\s*(?:is|è|e'|:|=)\s*(\S{4,})",
    re.IGNORECASE,
)

# Card-shaped: three or more groups of exactly four digits. Caught whether or
# not the whole passes Luhn, because a mistyped card is still mostly a real
# card, and a card split across two messages is a run of groups in each. The
# grouping is what keeps it off ordinary numbers: phone numbers (347 123
# 4567), dates, amounts and contract numbers (FP-2023-048812) are not 4-4-4.
_CARD_GROUPS = re.compile(
    r"(?<![\d.,/-])\d{4}(?:[ .-]\d{4}){2,}(?![\d/])(?:[ .-]\d{1,4}(?![\d/]))?"
)

# Digits spelled out: "four one one one one one ...". Converted, then held to
# the same checks as digits.
_DIGIT_WORDS = {
    "zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
}  # fmt: skip
_DIGIT_WORD_RUN = re.compile(
    rf"\b(?:{'|'.join(_DIGIT_WORDS)})(?:[\s,-]+(?:{'|'.join(_DIGIT_WORDS)})){{12,}}\b",
    re.IGNORECASE,
)


def _iban_valid(candidate: str) -> bool:
    """mod-97, per ISO 13616. An Italian IBAN is 27 characters."""
    value = re.sub(r"\s", "", candidate).upper()
    if not 15 <= len(value) <= 34:
        return False
    rearranged = value[4:] + value[:4]
    digits = "".join(str(int(c, 36)) if c.isalpha() else c for c in rearranged)
    try:
        return int(digits) % 97 == 1
    except ValueError:
        return False


def _luhn_valid(candidate: str) -> bool:
    digits = [int(c) for c in re.sub(r"[ -]", "", candidate) if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


_CF_ODD = {
    **{str(i): v for i, v in enumerate([1, 0, 5, 7, 9, 13, 15, 17, 19, 21])},
    **{
        c: v
        for c, v in zip(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            [
                1,
                0,
                5,
                7,
                9,
                13,
                15,
                17,
                19,
                21,
                2,
                4,
                18,
                20,
                11,
                3,
                6,
                8,
                12,
                14,
                16,
                10,
                22,
                25,
                24,
                23,
            ],
            strict=True,
        )
    },
}
_CF_EVEN = {
    **{str(i): i for i in range(10)},
    **{c: i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")},
}


def _codice_fiscale_valid(candidate: str) -> bool:
    value = candidate.upper()
    if len(value) != 16:
        return False
    try:
        total = sum(_CF_ODD[c] if i % 2 == 0 else _CF_EVEN[c] for i, c in enumerate(value[:15]))
    except KeyError:
        return False
    return "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[total % 26] == value[15]


# --- candidates --------------------------------------------------------------


def _candidates(text: str, start: int, allowed: str, separators: str, max_chars: int):
    """Every (end, compact) a value starting at `start` could end at.

    Walks forward over `allowed` characters, tolerating single separators
    between them (IBANs are written in groups of four; cards in groups of four
    with spaces or dashes). Yields every prefix, flagged with whether it ends
    at a boundary. The caller validates with the checksum, so what follows the
    value — a word, an expiry date — can no longer make a real value fail.
    """
    compact: list[str] = []
    i = start
    while i < len(text) and len(compact) < max_chars:
        char = text[i]
        if char in allowed:
            compact.append(char)
            i += 1
            # End of text is a boundary. (Not `"" not in allowed`: the empty
            # string is in every string, which silently made it never one.)
            yield i, "".join(compact), i == len(text) or text[i] not in allowed
        elif char in separators and compact and i + 1 < len(text) and text[i + 1] in allowed:
            i += 1
        else:
            break


_ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_DIGITS = "0123456789"


def _find_ibans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _IBAN_START.finditer(text):
        start = match.start()
        if spans and start < spans[-1][1]:
            continue
        expected = _IBAN_LENGTHS.get(match.group()[:2].upper())
        best = None
        for end, compact, boundary in _candidates(text, start, _ALNUM, " -.", 34):
            if expected is not None:
                # The country fixes the length: accept it there even if a word
                # is glued on ("…456thanks").
                if len(compact) == expected:
                    best = end if _iban_valid(compact) else None
                    break
            elif boundary and _iban_valid(compact):
                best = end  # no known length: keep the longest valid
        if best is not None:
            spans.append((start, best))
    return spans


def _find_cards(text: str, taken: list[tuple[int, int]]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for match in _CARD_START.finditer(text):
        start = match.start()
        if any(a <= start < b for a, b in taken + spans):
            continue
        best = None
        for end, compact, boundary in _candidates(text, start, _DIGITS, " -.", 19):
            # A group boundary is enough: in "4111 1111 1111 1111 12/27" the
            # card ends before the " 12", and that prefix is what validates.
            if boundary and len(compact) >= 13 and _luhn_valid(compact):
                best = end  # the longest valid: over-redacting an expiry month
                # is a smaller harm than leaving card digits in the clear
        if best is not None:
            spans.append((start, best))
    return spans


# --- detection ---------------------------------------------------------------


def find(text: str) -> list[Finding]:
    """Everything sensitive in `text`, validated, left to right."""
    findings: list[Finding] = []

    ibans = _find_ibans(text)
    findings += [Finding(SensitiveKind.iban, a, b) for a, b in ibans]
    # An IBAN contains long digit runs; do not report it twice.
    cards = _find_cards(text, ibans)
    for match in _CARD_GROUPS.finditer(text):
        span = match.span()
        if not any(a < span[1] and span[0] < b for a, b in ibans + cards):
            cards.append(span)
    # Spelled out, a run of 13–19 digit words is card-length. No Luhn gate:
    # nobody reads out that many digits in a row for any other reason.
    cards += _spelled_cards(text)
    findings += [Finding(SensitiveKind.card, a, b) for a, b in cards]

    for match in _CVV.finditer(text):
        findings.append(Finding(SensitiveKind.cvv, match.start(1), match.end(1)))
    for _, end in cards:
        after = _CVV_AFTER_CARD.match(text[end:])
        if after:
            findings.append(Finding(SensitiveKind.cvv, end + after.start(1), end + after.end(1)))

    for match in _CODICE_FISCALE.finditer(text):
        if _codice_fiscale_valid(match.group()):
            findings.append(Finding(SensitiveKind.codice_fiscale, match.start(), match.end()))

    for match in _PASSWORD.finditer(text):
        findings.append(Finding(SensitiveKind.password, match.start(1), match.end(1)))

    return sorted(findings, key=lambda f: f.start)


def _spelled_cards(text: str) -> list[tuple[int, int]]:
    spans = []
    for match in _DIGIT_WORD_RUN.finditer(text):
        digits = "".join(
            _DIGIT_WORDS[w.lower()] for w in re.findall(r"[a-z]+", match.group(), re.I)
        )
        if 13 <= len(digits) <= 19:
            spans.append((match.start(), match.end()))
    return spans


def redact(text: str) -> tuple[str, list[SensitiveKind]]:
    """Replace findings with placeholders. Returns the text and the *kinds*.

    The kinds are what gets stored and what the model is told — never the
    values. A model that knows an IBAN was pasted can correctly say "never send
    me your IBAN in chat"; a model that has the digits can repeat them.
    """
    findings = find(text)
    if not findings:
        return text, []

    out: list[str] = []
    cursor = 0
    for finding in findings:
        if finding.start < cursor:  # overlapping match, already covered
            continue
        out.append(text[cursor : finding.start])
        out.append(finding.placeholder)
        cursor = finding.end
    out.append(text[cursor:])

    return "".join(out), sorted({f.kind for f in findings})


def find_kinds(text: str) -> list[SensitiveKind]:
    """What kinds are present, without altering the text.

    For the one path that must *know* a value was pasted while deliberately not
    removing it — inbound redaction switched off (§9, DECISIONS #16). The
    conversation still records that an IBAN was seen, so the agent still tells
    the customer not to send one.
    """
    return sorted({f.kind for f in find(text)})


def contains_sensitive(text: str) -> bool:
    return bool(find(text))

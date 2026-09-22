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
    card = "carta di credito"
    cvv = "CVV"
    codice_fiscale = "codice fiscale"
    password = "password"


@dataclass(frozen=True, slots=True)
class Finding:
    kind: SensitiveKind
    start: int
    end: int

    @property
    def placeholder(self) -> str:
        return f"[[REDATTO:{self.kind.name.upper()}]]"


# --- patterns ----------------------------------------------------------------

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}[ ]?(?:[A-Z0-9][ ]?){11,30}\b", re.IGNORECASE)
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_CVV = re.compile(r"(?:cvv|cv2|cvc|codice di sicurezza)\D{0,12}(\d{3,4})\b", re.IGNORECASE)
_CODICE_FISCALE = re.compile(r"\b[A-Z]{6}\d{2}[A-EHLMPR-T]\d{2}[A-Z]\d{3}[A-Z]\b", re.IGNORECASE)
_PASSWORD = re.compile(r"(?:password|pwd|pin)\s*(?:è|e'|:|=)\s*(\S{4,})", re.IGNORECASE)


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


# --- detection ---------------------------------------------------------------


def find(text: str) -> list[Finding]:
    """Everything sensitive in `text`, validated, left to right."""
    findings: list[Finding] = []

    for match in _IBAN.finditer(text):
        if _iban_valid(match.group()):
            findings.append(Finding(SensitiveKind.iban, match.start(), match.end()))

    for match in _CARD.finditer(text):
        # An IBAN contains long digit runs; do not report it twice.
        if any(f.start <= match.start() < f.end for f in findings):
            continue
        if _luhn_valid(match.group()):
            findings.append(Finding(SensitiveKind.card, match.start(), match.end()))

    for match in _CVV.finditer(text):
        findings.append(Finding(SensitiveKind.cvv, match.start(1), match.end(1)))

    for match in _CODICE_FISCALE.finditer(text):
        if _codice_fiscale_valid(match.group()):
            findings.append(Finding(SensitiveKind.codice_fiscale, match.start(), match.end()))

    for match in _PASSWORD.finditer(text):
        findings.append(Finding(SensitiveKind.password, match.start(1), match.end(1)))

    return sorted(findings, key=lambda f: f.start)


def redact(text: str) -> tuple[str, list[SensitiveKind]]:
    """Replace findings with placeholders. Returns the text and the *kinds*.

    The kinds are what gets stored and what the model is told — never the
    values. A model that knows an IBAN was pasted can correctly say "non mi
    mandi mai l'IBAN in chat"; a model that has the digits can repeat them.
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


def contains_sensitive(text: str) -> bool:
    return bool(find(text))

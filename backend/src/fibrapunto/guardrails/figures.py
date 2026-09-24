"""What counts as a billing figure in free text: amounts and dates.

Split out of the grounding guard because the extraction is the half that fails
quietly. A figure the extractor does not recognise is never checked — it is not
blocked, not counted, not logged. So the forms are enumerated generously, on
both sides of the comparison: the reply, and the customer's own turns that
ground it.

Amounts come out as `Decimal`, whatever their spelling: "€29.90", "29,90 €",
"EUR 30", "30 euros", "€30", "twenty-nine euros ninety", "90 cents".

Dates come out as `DateMention`: whatever of (year, month, day) the text
actually states, so "19 September" is not silently promoted to a year it never
named, and "19 September 2025" is checked against the year as well. ISO
("2026-09-19") is read year-month-day — the agent's own tool results use it.
Relative forms that still assert a date ("the 19th", "end of the month",
"mid-September") are mentions too, with a coarser grounding rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

# --- number words ------------------------------------------------------------

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
_SCALES = {"hundred": 100, "thousand": 1000}
_NUMBER_WORDS = {**_UNITS, **_TENS, **_SCALES}

_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11,
    "twelfth": 12, "thirteenth": 13, "fourteenth": 14, "fifteenth": 15,
    "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
    "twentieth": 20, "thirtieth": 30,
}  # fmt: skip

_W = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
# "twenty-nine", "one hundred and twenty", "a hundred".
_WORDNUM = rf"(?:a\s+)?(?:{_W})(?:(?:\s+and\s+|[\s-]+)(?:{_W}))*"

_ORD_W = "|".join(sorted(_ORDINAL_WORDS, key=len, reverse=True))
_TENS_W = "|".join(_TENS)
# "nineteenth", "twenty-first", "thirty first".
_ORDWORD = rf"(?:(?:{_TENS_W})[\s-]+)?(?:{_ORD_W})"


def _words_to_int(text: str) -> int | None:
    total, current = 0, 0
    for token in re.split(r"[\s-]+", text.lower()):
        if token in ("and", ""):
            continue
        if token == "a":
            current = current or 1
        elif token in _UNITS or token in _TENS:
            current += _UNITS.get(token, 0) + _TENS.get(token, 0)
        elif token == "hundred":
            current = (current or 1) * 100
        elif token == "thousand":
            total += (current or 1) * 1000
            current = 0
        else:
            return None
    return total + current


def _ordinal_to_int(text: str) -> int | None:
    tokens = [t for t in re.split(r"[\s-]+", text.lower()) if t]
    if not tokens or tokens[-1] not in _ORDINAL_WORDS:
        return None
    value = _ORDINAL_WORDS[tokens[-1]]
    for token in tokens[:-1]:
        if token not in _TENS:
            return None
        value += _TENS[token]
    return value


# --- amounts -----------------------------------------------------------------

# Digits with optional grouping and decimals: 30 / 29.90 / 29,90 / 1,234.56.
_NUM = r"\d{1,3}(?:[.,   ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_CUR = r"(?:€|\beur\b|\beuros?\b)"
# Trailing cents without the word "cents" only at the end of a clause: in
# "30 euros 12 months", the 12 is not cents.
_CENTS_END = r"(?=\s*(?:[.,;:!?)]|$))"
# ...except a tens word, which is never a count of something: "twenty-nine
# euros ninety was refused" is €29.90, where "thirty euros two instalments" is
# not €30.02.
_TENS_CENTS = rf"(?:{'|'.join(_TENS)})(?:[\s-]+(?:{'|'.join(list(_UNITS)[1:10])}))?"

_AMOUNT_PATTERNS = (
    # €29.90 / EUR 30 / € 29,90. Symbols only as a prefix: "euros 30" is not
    # English, and allowing it reads "30 euros 12 months" as €12.
    re.compile(rf"(?:€|\beur\b)\s*(?P<n>{_NUM})(?!\d)", re.IGNORECASE),
    # 29.90 € / 30 euros / 29 euros 90 / 29 euros and 90 cents
    re.compile(
        rf"(?<![\d.,])(?P<n>{_NUM})\s*{_CUR}(?:\s+(?:and\s+)?(?P<c>\d{{2}}){_CENTS_END}|"
        rf"\s+(?:and\s+)?(?P<c2>\d{{1,2}})\s*cents?\b)?",
        re.IGNORECASE,
    ),
    # twenty-nine euros / twenty-nine euros ninety / thirty euros and fifty cents
    re.compile(
        rf"\b(?P<w>{_WORDNUM})\s+euros?\b(?:\s+(?:and\s+)?(?P<cw>{_WORDNUM}|\d{{1,2}})"
        rf"(?:\s*cents?\b|{_CENTS_END})|\s+(?P<ct>{_TENS_CENTS})\b)?",
        re.IGNORECASE,
    ),
    # 90 cents / ninety cents
    re.compile(rf"(?<![\d.,])\b(?P<cents>\d{{1,2}}|{_WORDNUM})\s+cents?\b", re.IGNORECASE),
    # A bare 29.90: two decimals reads as money even without a symbol. A full
    # stop after it ends the sentence; it is not more number.
    re.compile(r"(?<![\d.,/])(?P<n>\d{1,3}(?:[,\s]\d{3})*[.,]\d{2})(?![\d/]|[.,]\d)"),
)


def _decimal(raw: str) -> Decimal | None:
    """30 / 29.90 / 29,90 / 1,234.56 / 1.234,56 → Decimal.

    The last separator followed by one or two digits is the decimal one;
    anything else is grouping. Both conventions appear: the agent writes
    €29.90, a customer may type either.
    """
    cleaned = re.sub(r"[   ]", "", raw)
    match = re.search(r"[.,](\d{1,2})$", cleaned)
    if match:
        whole = re.sub(r"[.,]", "", cleaned[: match.start()])
        cleaned = f"{whole}.{match.group(1)}"
    else:
        cleaned = re.sub(r"[.,]", "", cleaned)
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _cents_value(raw: str | None) -> Decimal:
    if not raw:
        return Decimal(0)
    value = int(raw) if raw.isdigit() else _words_to_int(raw)
    return Decimal(value or 0) / 100


@dataclass(frozen=True, slots=True)
class Amount:
    value: Decimal
    text: str


def amounts(text: str) -> list[Amount]:
    found: list[Amount] = []
    taken: list[tuple[int, int]] = []

    def free(span: tuple[int, int]) -> bool:
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    for pattern in _AMOUNT_PATTERNS:
        for match in pattern.finditer(text):
            if not free(match.span()):
                continue
            groups = match.groupdict()
            value: Decimal | None
            if groups.get("n"):
                value = _decimal(groups["n"])
                if value is not None and "." not in str(value):
                    value += _cents_value(groups.get("c") or groups.get("c2"))
            elif groups.get("w"):
                whole = _words_to_int(groups["w"])
                cents = groups.get("cw") or groups.get("ct")
                value = None if whole is None else Decimal(whole) + _cents_value(cents)
            else:
                value = _cents_value(groups.get("cents"))
            if value is not None:
                taken.append(match.span())
                found.append(Amount(value, match.group().strip()))
    return found


# --- dates -------------------------------------------------------------------

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))
_MONTH = rf"(?P<mon>{_MON})\.?"
_DAY = rf"(?P<day>\d{{1,2}}(?:st|nd|rd|th)?|{_ORDWORD})"
_YEAR = r"(?P<year>(?:19|20)\d{2})"

Edge = Literal["start", "middle", "end"]


@dataclass(frozen=True, slots=True)
class DateMention:
    """What the text asserts about a date — only the parts it actually names.

    `edge` marks a relative form: "end of the month" is (edge="end",
    month=None); "mid-September" is (edge="middle", month=9).
    """

    year: int | None = None
    month: int | None = None
    day: int | None = None
    edge: Edge | None = None
    text: str = ""

    def __str__(self) -> str:
        return self.text


def _day(raw: str) -> int | None:
    digits = re.match(r"\d+", raw)
    value = int(digits.group()) if digits else _ordinal_to_int(raw)
    return value if value and 1 <= value <= 31 else None


def _valid(year: int | None, month: int, day: int | None) -> bool:
    if not 1 <= month <= 12:
        return False
    if day is None:
        return True
    try:
        date(year or 2024, month, day)  # 2024: a leap year, so 29 Feb is possible
    except ValueError:
        return False
    return True


_DATE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # 2026-09-19, 2026/09/19 — year first, always.
    ("iso", re.compile(r"(?<![\w-])(?P<year>\d{4})[-/](?P<m>\d{1,2})[-/](?P<d>\d{1,2})(?![\w-])")),
    # 19/09/2026, 09/19/26, 19.09.2026, 19-09-2026 — order ambiguous, both kept.
    (
        "numeric",
        re.compile(
            r"(?<![\w.,/-])(?P<a>\d{1,2})(?P<sep>[/.-])(?P<b>\d{1,2})(?P=sep)"
            r"(?P<year>\d{4}|\d{2})(?![\w/-]|[.,]\d)"
        ),
    ),
    # 19/09 or 09/19 — slash only: "19.09" is as likely to be money.
    ("numeric", re.compile(r"(?<![\w.,/-])(?P<a>\d{1,2})/(?P<b>\d{1,2})(?![\w/-]|[.,]\d)")),
    # 2026-09 — a month (invoice_month is written this way).
    ("iso_month", re.compile(r"(?<![\w-])(?P<year>\d{4})-(?P<m>\d{2})(?![\w-])")),
    # 19 September 2026 / 19th of Sep / the nineteenth of September
    (
        "dmy",
        re.compile(rf"\b{_DAY}\s+(?:of\s+)?{_MONTH}(?:,?\s+{_YEAR})?\b", re.IGNORECASE),
    ),
    # September 19, 2026 / Sep 19th / September the 19th
    (
        "mdy",
        re.compile(
            rf"\b{_MONTH}\s+(?:the\s+)?{_DAY}(?!\d)(?:,?\s+{_YEAR})?\b(?!\s*(?:€|eur|euros?|cents?)\b)",
            re.IGNORECASE,
        ),
    ),
    # end of the month / start of next month / end of September / mid-September
    (
        "edge",
        re.compile(
            rf"\b(?P<edge>end|beginning|start|middle)\s+of\s+(?:the\s+|this\s+|next\s+)?"
            rf"(?:(?:{_MONTH})|month)\b|\bmonth[-\s]end\b",
            re.IGNORECASE,
        ),
    ),
    ("mid", re.compile(rf"\bmid[-\s]?{_MONTH}\b", re.IGNORECASE)),
    # September 2026 — a month with its year.
    ("month_year", re.compile(rf"\b{_MONTH}\s+{_YEAR}\b", re.IGNORECASE)),
    # the 19th — a day of some month. Numeric ordinals only: "the first time"
    # and "the second instalment" are prose, not dates.
    ("the_day", re.compile(r"\bthe\s+(?P<day>\d{1,2}(?:st|nd|rd|th))\b", re.IGNORECASE)),
)

_EDGE: dict[str, Edge] = {"end": "end", "beginning": "start", "start": "start", "middle": "middle"}


def _year(raw: str | None) -> int | None:
    if not raw:
        return None
    value = int(raw)
    return value + 2000 if value < 100 else value


def dates(text: str) -> list[list[DateMention]]:
    """Every date the text asserts.

    Each entry is a list of readings. Almost always one; two when the text is
    genuinely ambiguous ("09/10/2026" is 9 October or 10 September), and the
    mention is grounded if any reading is.
    """
    found: list[list[DateMention]] = []
    taken: list[tuple[int, int]] = []

    def free(span: tuple[int, int]) -> bool:
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    for kind, pattern in _DATE_PATTERNS:
        for match in pattern.finditer(text):
            if not free(match.span()):
                continue
            g = match.groupdict()
            raw = match.group().strip()
            month = _MONTHS[g["mon"].lower()] if g.get("mon") else None
            readings: list[DateMention] = []

            if kind in ("iso", "iso_month"):
                year, m = int(g["year"]), int(g["m"])
                day = int(g["d"]) if g.get("d") else None
                if _valid(year, m, day):
                    readings.append(DateMention(year, m, day, text=raw))
            elif kind == "numeric":
                a, b, year = int(g["a"]), int(g["b"]), _year(g.get("year"))
                for day, m in {(a, b), (b, a)}:
                    if _valid(year, m, day):
                        readings.append(DateMention(year, m, day, text=raw))
            elif kind in ("dmy", "mdy"):
                day = _day(g["day"])
                if day and month and _valid(_year(g.get("year")), month, day):
                    readings.append(DateMention(_year(g.get("year")), month, day, text=raw))
            elif kind == "month_year":
                readings.append(DateMention(_year(g["year"]), month, text=raw))
            elif kind == "edge":
                edge = _EDGE[g["edge"].lower()] if g.get("edge") else "end"
                readings.append(DateMention(month=month, edge=edge, text=raw))
            elif kind == "mid":
                readings.append(DateMention(month=month, edge="middle", text=raw))
            elif kind == "the_day":
                day = _day(g["day"])
                if day:
                    readings.append(DateMention(day=day, text=raw))

            if readings:
                taken.append(match.span())
                found.append(readings)
    return found


def date_spans(text: str) -> str:
    """`text` with every recognised date blanked out, so "19.09.2026" is not
    also read as the amount 19.09."""
    out = text
    for _, pattern in _DATE_PATTERNS:
        out = pattern.sub(lambda m: " " * len(m.group()), out)
    return out


# --- grounding rules ---------------------------------------------------------

_EDGE_DAYS: dict[Edge, range] = {
    "start": range(1, 11),
    "middle": range(11, 21),
    "end": range(21, 32),
}


def supports(value: DateMention, claim: DateMention) -> bool:
    """Does `value` (something verified or said by the customer) support `claim`?

    Every part the claim names must be named, identically, by the value: a
    claim may be vaguer than its source, never more specific. "19 September"
    is supported by 2026-09-19; "19 September 2025" is not; "19 September" is
    not supported by the customer's "the 19th", because the month came from
    nowhere.
    """
    if claim.edge is not None:
        if value.edge is not None:
            return value.edge == claim.edge and value.month == claim.month
        if value.day is None or value.day not in _EDGE_DAYS[claim.edge]:
            return False
        return claim.month is None or claim.month == value.month
    if value.edge is not None:
        return False
    for part in ("year", "month", "day"):
        wanted = getattr(claim, part)
        if wanted is not None and getattr(value, part) != wanted:
            return False
    return True

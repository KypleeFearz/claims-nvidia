"""When is a value read by a reader right? (issue #19)

The evaluation harness (`app/docintel/evaluate.py`) compares each field of a reader
with the answer key (`expected.json`) through `compare_field`. The kind of a field
comes from its schema (issue #17), never from the value: an identifier is never
read as a number, an amount is never compared as text.

Rules, in short:

- A missing value is `null` or blank text. A missing value is right only when the
  answer key also has `None`.
- Amount: compared in cents (half up). A number, a `Decimal` or a plain, comma-grouped
  or apostrophe-grouped string is read. A currency sign, a decimal comma, a bool or
  NaN is "not an amount".
- Date: compared as a calendar day. ISO, Swiss day-first (`10.8.2026`) and English
  (`10 Aug 2026`, `August 10, 2026`) are read. A slash date, a two-digit year and a
  `datetime` are "not a date".
- Identifier: a string only. Case, outer spaces, Unicode forms and dash characters
  do not count. Inner spaces and zeros do count.
- Text: a string only. Case, dash characters and runs of white space do not count.
  Accents and punctuation do count. There is no fuzzy match.
- Lines are compared by position: one wrong position is one wrong line.
- A bad value in the answer key raises `BadAnswerKey`: the key is wrong, not the reader.
"""

import math
import re
import unicodedata
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from types import NoneType
from typing import Any, Literal, NamedTuple, get_args

Kind = Literal["amount", "date", "identifier", "text"]
Status = Literal["right", "wrong", "missing"]


class Verdict(NamedTuple):
    status: Status
    reason: str | None  # None when right; a short plain text otherwise


class LinesResult(NamedTuple):
    expected: int
    read: int
    right: int  # lines right at the same position
    all_right: bool  # same number of lines, and every line right


class BadAnswerKey(ValueError):
    """The expected value is not a valid value of its kind: the answer key is wrong."""


_IDENTIFIER_SUFFIXES = ("_number", "_reference")
_CENT = Decimal("0.01")

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_SWISS = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", re.ASCII)
_DAY_FIRST = re.compile(r"(\d{1,2}) ([A-Za-z]+) (\d{4})", re.ASCII)
_MONTH_FIRST = re.compile(r"([A-Za-z]+) (\d{1,2}), (\d{4})", re.ASCII)
_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "january february march april may june july "
            "august september october november december"
        ).split(),
        start=1,
    )
}
_MONTHS.update({name[:3]: number for name, number in list(_MONTHS.items())})
_MONTHS["sept"] = 9  # the other usual short name of September

_AMOUNT_FORMS = (
    re.compile(r"[+-]?\d+(\.\d+)?", re.ASCII),
    re.compile(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?", re.ASCII),
    re.compile(r"[+-]?\d{1,3}(\'\d{3})+(\.\d+)?", re.ASCII),
)

# Hyphen, non-breaking hyphen, figure dash, en dash, em dash, horizontal bar,
# minus sign, small hyphen-minus, small hyphen, full-width hyphen-minus.
_DASHES = {
    ord(c): "-" for c in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\ufe58\ufe63\uff0d"
}


def field_kind(document_type: str, name: str) -> Kind | None:
    """The kind of a field, from the annotation in its schema. None: no kind."""
    from app.docintel.schemas import SCHEMAS
    from app.models import DocumentType

    try:
        schema = SCHEMAS[DocumentType(document_type)]
    except (ValueError, KeyError):
        return None
    info = schema.model_fields.get(name)
    if info is None or name == "line_items":
        return None
    members = set(get_args(info.annotation))
    if members == {float, NoneType}:
        return "amount"
    if members == {date, NoneType}:
        return "date"
    if members == {str, NoneType}:
        return "identifier" if name.endswith(_IDENTIFIER_SUFFIXES) else "text"
    raise TypeError(f"no comparison kind for {document_type}.{name}: {info.annotation}")


def _is_real_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def check_expected(kind: Kind, expected: Any) -> None:
    """Raise `BadAnswerKey` when an answer-key value is not valid for its kind."""
    if expected is None:
        return
    if kind == "amount":
        if (
            isinstance(expected, bool)
            or not isinstance(expected, int | float)
            or not math.isfinite(expected)
        ):
            raise BadAnswerKey(f"amount must be a finite number, not {expected!r}")
    elif kind == "date":
        if isinstance(expected, datetime):
            raise BadAnswerKey(f"date must not carry a time: {expected!r}")
        if isinstance(expected, date):
            return
        if not isinstance(expected, str) or not (
            _ISO.fullmatch(expected) and _is_real_date(expected)
        ):
            raise BadAnswerKey(f"date must be a real YYYY-MM-DD date, not {expected!r}")
    elif not isinstance(expected, str) or not expected.strip():
        raise BadAnswerKey(f"{kind} must be a text that is not blank, not {expected!r}")


def _to_cents(value: Any) -> Decimal | None:
    """The amount in cents, or None when the value is not an amount."""
    number: Decimal
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None
        number = Decimal(repr(value))
    elif isinstance(value, Decimal):
        number = value
    elif isinstance(value, str):
        text = value.strip().replace("\u2019", "'")
        if not any(form.fullmatch(text) for form in _AMOUNT_FORMS):
            return None
        number = Decimal(text.replace(",", "").replace("'", ""))
    else:
        return None
    if not number.is_finite():
        return None
    try:
        return number.quantize(_CENT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def _to_date(value: Any) -> date | None:
    """The calendar day, or None when the value is not a date in a known format."""
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        if _ISO.fullmatch(text):
            return date.fromisoformat(text)
        if match := _SWISS.fullmatch(text):
            day, month, year = match.groups()
            return date(int(year), int(month), int(day))
        if match := _DAY_FIRST.fullmatch(text):
            day, month_name, year = match.groups()
        elif match := _MONTH_FIRST.fullmatch(text):
            month_name, day, year = match.groups()
        else:
            return None
        month_number = _MONTHS.get(month_name.lower())
        if month_number is None:
            return None
        return date(int(year), month_number, int(day))
    except ValueError:
        return None


def _identifier_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).translate(_DASHES).strip().casefold()


def _text_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).translate(_DASHES)
    return " ".join(text.split()).casefold()


def compare_field(kind: Kind, expected: Any, read: Any) -> Verdict:
    """Is the value read right? Raises `BadAnswerKey` when the answer key is bad."""
    check_expected(kind, expected)
    missing: Verdict | None
    if read is None:
        missing = Verdict("missing", "null")
    elif isinstance(read, str) and not read.strip():
        missing = Verdict("missing", "blank")
    else:
        missing = None
    if expected is None:
        if missing is not None:
            return Verdict("right", None)
        return Verdict("wrong", "expected no value")
    if missing is not None:
        return missing

    if kind == "amount":
        want_cents = _to_cents(expected)
        got_cents = _to_cents(read)
        if got_cents is None:
            return Verdict("wrong", "not an amount")
        if got_cents == want_cents:
            return Verdict("right", None)
        return Verdict("wrong", f"expected {want_cents}, read {got_cents}")
    if kind == "date":
        want_day = _to_date(expected)
        got_day = _to_date(read)
        if want_day is None or got_day is None:
            return Verdict("wrong", "not a date")
        if got_day == want_day:
            return Verdict("right", None)
        return Verdict(
            "wrong", f"expected {want_day.isoformat()}, read {got_day.isoformat()}"
        )
    if not isinstance(read, str):
        return Verdict("wrong", "not a text value")
    key = _identifier_key if kind == "identifier" else _text_key
    if key(read) == key(expected):
        return Verdict("right", None)
    return Verdict("wrong", "different text")


_AMOUNT_LINE_KEYS = {"quantity", "unit_price", "amount"}


def _line_kind(key: str) -> Kind:
    return "amount" if key in _AMOUNT_LINE_KEYS else "text"


def compare_lines(expected: list[Any], read: list[Any]) -> LinesResult:
    """Compare line items by position. Extra keys in a read line are ignored."""
    for line in expected:
        for key, value in line.items():
            check_expected(_line_kind(key), value)
    lines = read if isinstance(read, list) else []
    right = 0
    for want, got in zip(expected, lines, strict=False):
        if isinstance(got, dict) and all(
            compare_field(_line_kind(key), value, got.get(key)).status == "right"
            for key, value in want.items()
        ):
            right += 1
    return LinesResult(
        expected=len(expected),
        read=len(lines),
        right=right,
        all_right=len(expected) == len(lines) and right == len(expected),
    )

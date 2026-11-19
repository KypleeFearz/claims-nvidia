"""The comparator of the evaluation harness (issue #19): when is a read value right?"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.docintel.compare import (
    BadAnswerKey,
    Kind,
    LinesResult,
    Verdict,
    check_expected,
    compare_field,
    compare_lines,
    field_kind,
)
from app.docintel.schemas import SCHEMAS
from app.models import DocumentType

IDENTIFIERS = {
    "booking_reference",
    "invoice_number",
    "quote_number",
    "report_number",
    "report_reference",
}


def _status(kind: Kind, expected: Any, read: Any) -> str:
    return compare_field(kind, expected, read).status


# --- field_kind -------------------------------------------------------------


def test_every_field_of_every_schema_has_a_kind() -> None:
    identifiers: set[str] = set()
    for document_type, schema in SCHEMAS.items():
        for name in schema.model_fields:
            if name == "line_items":
                assert field_kind(document_type.value, name) is None
                continue
            kind = field_kind(document_type.value, name)
            assert kind is not None, (document_type, name)
            if kind == "identifier":
                identifiers.add(name)
    assert identifiers == IDENTIFIERS


@pytest.mark.parametrize(
    ("document_type", "name", "kind"),
    [
        ("repair_invoice", "total", "amount"),
        ("cancellation_confirmation", "refund_received", "amount"),
        ("booking_confirmation", "departure_date", "date"),
        ("police_report", "incident_date", "date"),
        ("booking_confirmation", "booking_reference", "identifier"),
        ("repair_invoice", "invoice_number", "identifier"),
        ("police_report", "report_number", "identifier"),
        ("luggage_report", "report_reference", "identifier"),
        ("repair_invoice", "repair_company", "text"),
        ("medical_certificate", "cause", "text"),
    ],
)
def test_field_kind_by_annotation(document_type: str, name: str, kind: str) -> None:
    assert field_kind(document_type, name) == kind


@pytest.mark.parametrize(
    ("document_type", "name"),
    [
        ("unknown", "total"),
        ("not_a_type", "total"),
        ("repair_invoice", "no_such_field"),
        ("repair_invoice", "line_items"),
    ],
)
def test_field_kind_none_for_unknown(document_type: str, name: str) -> None:
    assert field_kind(document_type, name) is None


def test_field_kind_refuses_a_new_annotation(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import BaseModel

    class Odd(BaseModel):
        count: int | None

    monkeypatch.setitem(SCHEMAS, DocumentType.receipt, Odd)
    with pytest.raises(TypeError):
        field_kind("receipt", "count")


# --- check_expected ---------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("amount", None),
        ("date", None),
        ("identifier", None),
        ("text", None),
        ("amount", 1840),
        ("amount", 1840.5),
        ("amount", -150.0),
        ("date", "2026-08-10"),
        ("date", date(2026, 8, 10)),
        ("identifier", "WPR-482913"),
        ("text", "Anna Keller"),
    ],
)
def test_check_expected_accepts(kind: Kind, expected: Any) -> None:
    check_expected(kind, expected)


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("amount", "1840"),
        ("amount", True),
        ("amount", float("nan")),
        ("amount", float("inf")),
        ("amount", [1840]),
        ("date", "10.08.2026"),
        ("date", "2026-02-30"),
        ("date", "2026-8-10"),
        ("date", datetime(2026, 8, 10, 12, 0)),
        ("date", 20260810),
        ("identifier", ""),
        ("identifier", "   "),
        ("identifier", 482913),
        ("text", ""),
        ("text", "  \t"),
        ("text", 5),
    ],
)
def test_check_expected_refuses(kind: Kind, expected: Any) -> None:
    with pytest.raises(BadAnswerKey):
        check_expected(kind, expected)


def test_compare_field_checks_the_answer_key_first() -> None:
    with pytest.raises(BadAnswerKey):
        compare_field("amount", "1840", 1840.0)
    with pytest.raises(BadAnswerKey):
        compare_field("date", "10.08.2026", "2026-08-10")
    with pytest.raises(BadAnswerKey):
        compare_field("text", " ", "x")


# --- missing and expected None ----------------------------------------------


@pytest.mark.parametrize("kind", ["amount", "date", "identifier", "text"])
def test_null_read_is_missing(kind: Kind) -> None:
    expected: Any = {
        "amount": 1.0,
        "date": "2026-08-10",
        "identifier": "A-1",
        "text": "x",
    }[kind]
    assert compare_field(kind, expected, None) == Verdict("missing", "null")


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_blank_read_is_missing(blank: str) -> None:
    assert compare_field("text", "x", blank) == Verdict("missing", "blank")
    assert compare_field("amount", 1.0, blank) == Verdict("missing", "blank")


@pytest.mark.parametrize("kind", ["amount", "date", "identifier", "text"])
def test_expected_none_and_read_missing_is_right(kind: Kind) -> None:
    assert compare_field(kind, None, None) == Verdict("right", None)
    assert compare_field(kind, None, "  ") == Verdict("right", None)


@pytest.mark.parametrize(
    ("kind", "read"),
    [
        ("amount", 0),
        ("amount", "10"),
        ("date", "2026-08-10"),
        ("identifier", "A-1"),
        ("text", "x"),
    ],
)
def test_expected_none_and_value_read_is_wrong(kind: Kind, read: Any) -> None:
    assert compare_field(kind, None, read) == Verdict("wrong", "expected no value")


# --- amount -----------------------------------------------------------------


@pytest.mark.parametrize(
    "read",
    [
        1840.0,
        1840,
        "1840",
        "1840.00",
        "1,840.00",
        "1,840",
        "1'840.00",
        "1’840.00",
        " 1840.0 ",
        "+1840",
        Decimal("1840.00"),
        Decimal("1840"),
        1840.004,
        "1840.004",
    ],
)
def test_amount_right(read: Any) -> None:
    assert compare_field("amount", 1840.0, read) == Verdict("right", None)


def test_amount_expected_int_and_grouped_millions() -> None:
    assert _status("amount", 1234567, "1,234,567.00") == "right"
    assert _status("amount", 1234567.5, "1'234'567.50") == "right"


def test_amount_wrong_value_has_a_reason_with_cents() -> None:
    verdict = compare_field("amount", 1840, 1804)
    assert verdict == Verdict("wrong", "expected 1840.00, read 1804.00")


@pytest.mark.parametrize("read", [1840.01, 1839.99, "1,840.01", Decimal("1840.01")])
def test_amount_one_cent_off_is_wrong(read: Any) -> None:
    assert _status("amount", 1840.0, read) == "wrong"


def test_amount_sign_is_checked() -> None:
    assert _status("amount", 1500.0, -1500) == "wrong"
    assert _status("amount", -1500.0, 1500) == "wrong"
    assert _status("amount", -1500.0, "-1,500.00") == "right"


@pytest.mark.parametrize("read", [0, -0.0, "-0.00", "0", Decimal("0.00")])
def test_amount_zero(read: Any) -> None:
    assert _status("amount", 0, read) == "right"
    assert _status("amount", 0.0, read) == "right"


def test_amount_rounds_half_up_to_cents() -> None:
    assert _status("amount", 0.01, 0.005) == "right"
    assert _status("amount", 0.01, 0.0049) == "wrong"


@pytest.mark.parametrize(
    "read",
    [
        "CHF 1,840.00",
        "1840 CHF",
        "1.840,00",
        "1,84",
        "1,8400",
        "12,34,567",
        "1 840.00",
        "1,840.00.00",
        "1,,840",
        "1'840,00",
        "1,840'000",
        "abc",
        "1e3",
        "0x10",
        ".5",
        "5.",
        "--5",
        "NaN",
        "inf",
        "-inf",
        "Infinity",
        "1_000",
        "١٢٣",
        float("nan"),
        float("inf"),
        float("-inf"),
        Decimal("NaN"),
        Decimal("Infinity"),
        True,
        False,
        [1840],
        {"amount": 1840},
        (1840,),
        date(2026, 8, 10),
    ],
)
def test_amount_malformed_is_wrong(read: Any) -> None:
    assert compare_field("amount", 1840.0, read) == Verdict("wrong", "not an amount")


def test_amount_float_is_converted_through_repr() -> None:
    # Decimal(0.1 + 0.2) would carry binary noise; repr gives 0.30000000000000004.
    assert _status("amount", 0.3, 0.1 + 0.2) == "right"
    assert _status("amount", 19.99, 19.99) == "right"
    assert _status("amount", 1.005, "1.005") == "right"


# --- date -------------------------------------------------------------------


@pytest.mark.parametrize(
    "read",
    [
        "2026-08-10",
        " 2026-08-10 ",
        "10.8.2026",
        "10.08.2026",
        "10 August 2026",
        "10 Aug 2026",
        "10 aug 2026",
        "10 AUGUST 2026",
        "August 10, 2026",
        "Aug 10, 2026",
        "aug 10, 2026",
        date(2026, 8, 10),
    ],
)
def test_date_right(read: Any) -> None:
    assert compare_field("date", "2026-08-10", read) == Verdict("right", None)


def test_september_has_two_short_names() -> None:
    assert _status("date", "2026-09-22", "22 Sept 2026") == "right"
    assert _status("date", "2026-09-22", "22 Sep 2026") == "right"
    assert _status("date", "2026-09-22", "22 Septe 2026") == "wrong"


def test_date_expected_as_a_date_object() -> None:
    assert _status("date", date(2026, 8, 10), "10.08.2026") == "right"
    assert _status("date", date(2026, 8, 10), "2026-08-11") == "wrong"


@pytest.mark.parametrize(
    ("read", "iso"),
    [
        ("2026-08-11", "2026-08-11"),
        ("11.08.2026", "2026-08-11"),
        ("1 Jan 2025", "2025-01-01"),
    ],
)
def test_date_wrong_value_has_a_reason(read: str, iso: str) -> None:
    assert compare_field("date", "2026-08-10", read) == Verdict(
        "wrong", f"expected 2026-08-10, read {iso}"
    )


def test_date_day_and_month_not_swapped() -> None:
    assert _status("date", "2026-08-10", "08.10.2026") == "wrong"
    assert _status("date", "2026-10-08", "08.10.2026") == "right"


@pytest.mark.parametrize(
    "read",
    [
        "08/10/2026",
        "10/08/2026",
        "2026/08/10",
        "10.08.26",
        "10.08.",
        "2026-8-10",
        "2026-08-1",
        "20260810",
        "2026-W32-1",
        "2026-08-10T00:00:00",
        "2026-08-10 12:00",
        "2026-02-30",
        "31.04.2026",
        "30 February 2026",
        "Feb 30, 2026",
        "10 Augu 2026",
        "10 Sept. 2026",  # a short name with a dot is not read
        "August 10 2026",
        "10 August, 2026",
        "10 August 26",
        "Tuesday 10 August 2026",
        "tomorrow",
        "",
        20260810,
        1.5,
        True,
        ["2026-08-10"],
        datetime(2026, 8, 10, 0, 0),
    ],
)
def test_date_refused_forms_are_wrong(read: Any) -> None:
    verdict = compare_field("date", "2026-08-10", read)
    if read == "":
        assert verdict == Verdict("missing", "blank")
    else:
        assert verdict == Verdict("wrong", "not a date")


# --- identifier -------------------------------------------------------------


def test_identifier_exact_and_case() -> None:
    assert _status("identifier", "WPR-482913", "WPR-482913") == "right"
    assert _status("identifier", "WPR-482913", "wpr-482913") == "right"
    assert _status("identifier", "WPR-482913", "  WPR-482913\n") == "right"


@pytest.mark.parametrize(
    "read",
    [
        "WPR‑482913",
        "WPR‐482913",
        "WPR‒482913",
        "WPR–482913",
        "WPR—482913",
        "WPR―482913",
        "WPR−482913",
        "WPR﹘482913",
        "WPR﹣482913",
        "WPR－482913",
        "ＷＰＲ-482913",
    ],
)
def test_identifier_dash_variants_and_nfkc_are_right(read: str) -> None:
    assert _status("identifier", "WPR-482913", read) == "right"


@pytest.mark.parametrize(
    ("expected", "read"),
    [
        ("WPR-482913", "WPR 482913"),
        ("WPR-482913", "WPR482913"),
        ("WPR-482913", "WPR-482 913"),
        ("001840", "1840"),
        ("1840", "001840"),
        ("WPR-482913", "WPR-482914"),
        ("WPR-482913", "WPR-482913."),
        ("A-1", "A_1"),
    ],
)
def test_identifier_wrong(expected: str, read: str) -> None:
    assert compare_field("identifier", expected, read) == Verdict(
        "wrong", "different text"
    )


@pytest.mark.parametrize("read", [482913, 482913.0, True, ["A"], Decimal("1")])
def test_identifier_refuses_numbers(read: Any) -> None:
    assert compare_field("identifier", "482913", read) == Verdict(
        "wrong", "not a text value"
    )


# --- text -------------------------------------------------------------------


@pytest.mark.parametrize(
    "read",
    [
        "Anna Keller",
        "anna keller",
        "ANNA KELLER",
        "  Anna   Keller ",
        "Anna\tKeller",
        "Anna\nKeller",
        "Anna Keller",
    ],
)
def test_text_whitespace_and_case_are_right(read: str) -> None:
    assert _status("text", "Anna Keller", read) == "right"


def test_text_dash_variants_are_right() -> None:
    assert _status("text", "Meier-Muller AG", "Meier‑Muller AG") == "right"
    assert _status("text", "Meier-Muller AG", "Meier–Muller AG") == "right"


@pytest.mark.parametrize(
    ("expected", "read"),
    [
        ("Sanitär AG", "Sanitaer AG"),
        ("Sanitär AG", "Sanitar AG"),
        ("Anna Keller", "Anna Keller."),
        ("Anna Keller", "Keller Anna"),
        ("Anna Keller", "Anna Kelle"),
        ("Anna Keller", "AnnaKeller"),
        ("Anna Keller", "Anna Keler"),
    ],
)
def test_text_wrong(expected: str, read: str) -> None:
    assert compare_field("text", expected, read) == Verdict("wrong", "different text")


@pytest.mark.parametrize("read", [5, 5.0, True, ["Anna"], {"a": 1}, date(2026, 8, 10)])
def test_text_refuses_non_text(read: Any) -> None:
    assert compare_field("text", "5", read) == Verdict("wrong", "not a text value")


# --- compare_lines ----------------------------------------------------------

LINES: list[dict[str, Any]] = [
    {"description": "Pipe", "quantity": 2, "unit_price": 50.0, "amount": 100.0},
    {"description": "Labour", "quantity": 1, "unit_price": 240.0, "amount": 240.0},
]


def _read_lines() -> list[Any]:
    return [dict(line) for line in LINES]


def test_lines_all_right() -> None:
    assert compare_lines(LINES, _read_lines()) == LinesResult(2, 2, 2, True)


def test_lines_two_empty_lists() -> None:
    assert compare_lines([], []) == LinesResult(0, 0, 0, True)


def test_lines_one_line_wrong() -> None:
    read = _read_lines()
    read[1]["amount"] = 204.0
    assert compare_lines(LINES, read) == LinesResult(2, 2, 1, False)


def test_lines_missing_line() -> None:
    assert compare_lines(LINES, _read_lines()[:1]) == LinesResult(2, 1, 1, False)


def test_lines_extra_line() -> None:
    read = [*_read_lines(), {"description": "Extra", "amount": 5.0}]
    assert compare_lines(LINES, read) == LinesResult(2, 3, 2, False)


def test_lines_reordered_lines_are_wrong() -> None:
    read = list(reversed(_read_lines()))
    assert compare_lines(LINES, read) == LinesResult(2, 2, 0, False)


def test_lines_non_dict_line_is_wrong() -> None:
    assert compare_lines(LINES, [_read_lines()[0], "Labour 240"]) == LinesResult(
        2, 2, 1, False
    )
    assert compare_lines(LINES, [None, None]) == LinesResult(2, 2, 0, False)


def test_lines_quantity_two_equals_two_point_zero() -> None:
    read = _read_lines()
    read[0]["quantity"] = 2.0
    assert compare_lines(LINES, read) == LinesResult(2, 2, 2, True)


def test_lines_amount_as_grouped_string_is_right() -> None:
    expected = [{"description": "Roof", "amount": 1840.0}]
    read = [{"description": "roof", "amount": "1,840.00"}]
    assert compare_lines(expected, read) == LinesResult(1, 1, 1, True)


def test_lines_key_absent_in_the_read_line_is_wrong() -> None:
    read = _read_lines()
    del read[0]["unit_price"]
    assert compare_lines(LINES, read) == LinesResult(2, 2, 1, False)


def test_lines_null_expected_key_absent_in_read_is_right() -> None:
    expected = [{"description": "Fee", "quantity": None, "amount": 10.0}]
    read = [{"description": "Fee", "amount": 10.0}]
    assert compare_lines(expected, read) == LinesResult(1, 1, 1, True)


def test_lines_null_expected_key_with_a_value_is_wrong() -> None:
    expected = [{"description": "Fee", "quantity": None, "amount": 10.0}]
    read = [{"description": "Fee", "quantity": 1, "amount": 10.0}]
    assert compare_lines(expected, read) == LinesResult(1, 1, 0, False)


def test_lines_extra_keys_in_the_read_line_are_ignored() -> None:
    read = _read_lines()
    read[0]["note"] = "ignored"
    assert compare_lines(LINES, read) == LinesResult(2, 2, 2, True)


def test_lines_description_uses_text_rules() -> None:
    read = _read_lines()
    read[0]["description"] = "  PIPE "
    assert compare_lines(LINES, read).all_right is True
    read[0]["description"] = "Pipes"
    assert compare_lines(LINES, read).right == 1


@pytest.mark.parametrize("read", [None, 0, "text", {"a": 1}])
def test_lines_non_list_read_counts_as_empty(read: Any) -> None:
    assert compare_lines(LINES, read) == LinesResult(2, 0, 0, False)
    assert compare_lines([], read) == LinesResult(0, 0, 0, True)


def test_lines_bad_answer_key_propagates() -> None:
    with pytest.raises(BadAnswerKey):
        compare_lines([{"amount": "100"}], [{"amount": 100.0}])
    with pytest.raises(BadAnswerKey):
        compare_lines([{"description": " "}], [{"description": "x"}])

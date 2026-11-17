import json
from datetime import date
from pathlib import Path
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

from app.docintel.schemas import SCHEMAS
from app.models import DocumentType
from tests.reader_contract import MOCK

EXPECTED = sorted(MOCK.glob("**/*.expected.json"))
IDS = [p.relative_to(MOCK).as_posix() for p in EXPECTED]


def _read(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _input_json(expected: dict[str, Any], schema: type[BaseModel]) -> str:
    """The JSON a model would answer for this file: values only."""
    # An absent field means null (D77). An unknown name stays and is refused.
    body: dict[str, Any] = {n: None for n in schema.model_fields if n != "line_items"}
    body.update({f["name"]: f["value"] for f in expected["fields"]})
    if "line_items" in schema.model_fields:
        body["line_items"] = expected["line_items"]
    return json.dumps(body)


@pytest.mark.parametrize("path", EXPECTED, ids=IDS)
def test_every_expected_file_validates_against_its_schema(path: Path) -> None:
    expected = _read(path)
    schema = SCHEMAS[DocumentType(expected["document_type"])]
    names = [f["name"] for f in expected["fields"]]

    assert len(names) == len(set(names))
    if "line_items" not in schema.model_fields:
        assert expected["line_items"] == []
    schema.model_validate_json(_input_json(expected, schema))


def test_every_known_document_type_has_a_schema_and_unknown_has_none() -> None:
    assert set(SCHEMAS) == set(DocumentType) - {DocumentType.unknown}


def _kit_files_of(document_type: DocumentType) -> list[dict[str, Any]]:
    files = [_read(p) for p in EXPECTED]
    return [e for e in files if e["document_type"] == document_type]


@pytest.mark.parametrize("document_type", sorted(SCHEMAS), ids=str)
def test_a_schema_holds_exactly_the_fields_the_kit_files_of_its_type_have(
    document_type: DocumentType,
) -> None:
    kit = _kit_files_of(document_type)
    kit_names = {f["name"] for e in kit for f in e["fields"]}
    schema_names = set(SCHEMAS[document_type].model_fields) - {"line_items"}

    assert kit
    assert schema_names == kit_names


@pytest.mark.parametrize("document_type", sorted(SCHEMAS), ids=str)
def test_a_line_schema_holds_exactly_the_keys_the_kit_lines_of_its_type_have(
    document_type: DocumentType,
) -> None:
    kit = _kit_files_of(document_type)
    kit_keys = {k for e in kit for line in e["line_items"] for k in line}
    field = SCHEMAS[document_type].model_fields.get("line_items")

    if field is None:
        assert kit_keys == set()
    else:
        [line_schema] = get_args(field.annotation)
        assert set(line_schema.model_fields) == kit_keys


MISSING = object()  # a key to leave out of the input
BOOKING = SCHEMAS[DocumentType.booking_confirmation]
REPAIR_INVOICE = SCHEMAS[DocumentType.repair_invoice]
CANCELLATION = SCHEMAS[DocumentType.cancellation_confirmation]


def _booking(**changes: Any) -> str:
    path = MOCK / "claim-1-travel" / "booking-confirmation.expected.json"
    expected = _read(path)
    body = {f["name"]: f["value"] for f in expected["fields"]}
    body["line_items"] = expected["line_items"]
    body.update(changes)
    return json.dumps({k: v for k, v in body.items() if v is not MISSING})


def _errors(caught: pytest.ExceptionInfo[ValidationError]) -> list[tuple[Any, str]]:
    """Where and why a validation failed: a (location, type) pair per error."""
    return [(e["loc"], e["type"]) for e in caught.value.errors()]


def test_a_missing_key_is_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        BOOKING.model_validate_json(_booking(trip_price=MISSING))
    assert _errors(caught) == [(("trip_price",), "missing")]


def test_a_null_value_is_accepted_and_gives_none() -> None:
    booking = BOOKING.model_validate_json(_booking(trip_price=None))
    assert booking.model_dump()["trip_price"] is None


def test_null_line_items_are_refused_and_an_empty_list_is_accepted() -> None:
    with pytest.raises(ValidationError) as caught:
        BOOKING.model_validate_json(_booking(line_items=None))
    assert [loc for loc, _ in _errors(caught)] == [("line_items",)]
    booking = BOOKING.model_validate_json(_booking(line_items=[]))
    assert booking.model_dump()["line_items"] == []


def test_a_json_int_is_an_amount_and_a_date_string_is_a_date() -> None:
    booking = BOOKING.model_validate_json(
        _booking(trip_price=1840, departure_date="2026-08-10")
    ).model_dump()
    assert booking["trip_price"] == 1840.0
    assert isinstance(booking["trip_price"], float)
    assert booking["departure_date"] == date(2026, 8, 10)


@pytest.mark.parametrize(
    "changes",
    [
        {"trip_price": "1840"},
        {"trip_price": True},
        {"departure_date": "10.08.2026"},
        {"departure_date": 20260810},
        {"iban": "CH93 0076 2011 6238 5295 7"},
    ],
    ids=["string-amount", "bool-amount", "swiss-date", "int-date", "unknown-key"],
)
def test_a_wrong_type_or_an_unknown_key_is_refused(changes: dict[str, Any]) -> None:
    [key] = changes
    with pytest.raises(ValidationError) as caught:
        BOOKING.model_validate_json(_booking(**changes))
    errors = _errors(caught)
    assert errors
    assert all(loc[0] == key for loc, _ in errors)
    if key == "iban":
        assert errors == [(("iban",), "extra_forbidden")]


def _invoice(*lines: dict[str, Any]) -> str:
    return json.dumps(
        {
            "invoice_number": "R-1",
            "invoice_date": "2026-05-02",
            "repair_company": "Muster AG",
            "total": 300,
            "customer_name": "Anna Keller",
            "line_items": list(lines),
        }
    )


LINE = {"description": "Pipe", "quantity": 2, "unit_price": 150, "amount": 300}


@pytest.mark.parametrize("key", ["quantity", "unit_price"])
def test_a_standard_line_needs_every_key(key: str) -> None:
    line = {k: v for k, v in LINE.items() if k != key}
    with pytest.raises(ValidationError) as caught:
        REPAIR_INVOICE.model_validate_json(_invoice(line))
    assert _errors(caught) == [(("line_items", 0, key), "missing")]


def test_a_standard_line_takes_a_decimal_quantity_and_a_null() -> None:
    REPAIR_INVOICE.model_validate_json(_invoice({**LINE, "quantity": 1.5}))
    REPAIR_INVOICE.model_validate_json(_invoice({**LINE, "quantity": None}))


def test_a_standard_line_refuses_an_unknown_key() -> None:
    with pytest.raises(ValidationError) as caught:
        REPAIR_INVOICE.model_validate_json(_invoice({**LINE, "extra": 1}))
    assert _errors(caught) == [(("line_items", 0, "extra"), "extra_forbidden")]


def _cancellation(line: dict[str, Any]) -> str:
    return json.dumps(
        {
            "booking_reference": "WPR-482913",
            "cancellation_date": "2026-07-01",
            "refund_received": 340,
            "line_items": [line],
        }
    )


def test_a_cancellation_line_takes_a_negative_amount_and_no_quantity() -> None:
    fee = {"description": "Cancellation fees", "amount": -1500}
    CANCELLATION.model_validate_json(_cancellation(fee))
    with pytest.raises(ValidationError) as caught:
        CANCELLATION.model_validate_json(_cancellation({**fee, "quantity": 1}))
    assert _errors(caught) == [(("line_items", 0, "quantity"), "extra_forbidden")]


def test_a_cancellation_line_needs_its_amount() -> None:
    with pytest.raises(ValidationError) as caught:
        CANCELLATION.model_validate_json(_cancellation({"description": "Fees"}))
    assert _errors(caught) == [(("line_items", 0, "amount"), "missing")]


def _asks_for_every_key_and_allows_null(
    json_schema: dict[str, Any], skip: str | None = None
) -> None:
    assert json_schema["additionalProperties"] is False
    assert json_schema["required"] == list(json_schema["properties"])
    for name, prop in json_schema["properties"].items():
        if name != skip:
            assert {"type": "null"} in prop["anyOf"]


@pytest.mark.parametrize("document_type", sorted(SCHEMAS), ids=str)
def test_the_json_schema_of_a_type_asks_for_every_key_and_allows_null(
    document_type: DocumentType,
) -> None:
    schema = SCHEMAS[document_type]
    json_schema = schema.model_json_schema()

    assert json.loads(json.dumps(json_schema)) == json_schema
    assert json_schema["required"] == list(schema.model_fields)
    _asks_for_every_key_and_allows_null(json_schema, skip="line_items")
    if "line_items" in schema.model_fields:
        assert json_schema["properties"]["line_items"]["type"] == "array"
        assert "anyOf" not in json_schema["properties"]["line_items"]
        assert json_schema["$defs"]
    for line_schema in json_schema.get("$defs", {}).values():
        _asks_for_every_key_and_allows_null(line_schema)


NON_FINITE = [float("nan"), float("inf"), float("-inf")]


@pytest.mark.parametrize("value", NON_FINITE, ids=["nan", "inf", "minus-inf"])
def test_a_non_finite_trip_price_is_refused(value: float) -> None:
    with pytest.raises(ValidationError) as caught:
        BOOKING.model_validate_json(_booking(trip_price=value))
    assert _errors(caught) == [(("trip_price",), "finite_number")]


@pytest.mark.parametrize("value", NON_FINITE, ids=["nan", "inf", "minus-inf"])
def test_a_non_finite_repair_line_amount_is_refused(value: float) -> None:
    with pytest.raises(ValidationError) as caught:
        REPAIR_INVOICE.model_validate_json(_invoice({**LINE, "amount": value}))
    assert _errors(caught) == [(("line_items", 0, "amount"), "finite_number")]


@pytest.mark.parametrize("value", NON_FINITE, ids=["nan", "inf", "minus-inf"])
def test_a_non_finite_cancellation_line_amount_is_refused(value: float) -> None:
    line = {"description": "Cancellation fees", "amount": value}
    with pytest.raises(ValidationError) as caught:
        CANCELLATION.model_validate_json(_cancellation(line))
    assert _errors(caught) == [(("line_items", 0, "amount"), "finite_number")]


def test_a_number_too_big_for_a_float_is_refused() -> None:
    text = _booking(trip_price="TOO_BIG").replace('"TOO_BIG"', "1e400")
    with pytest.raises(ValidationError) as caught:
        BOOKING.model_validate_json(text)
    assert _errors(caught) == [(("trip_price",), "finite_number")]


BLANKS = ["", "   ", "\t\n"]


@pytest.mark.parametrize("value", BLANKS, ids=["empty", "spaces", "tab-newline"])
def test_a_blank_traveller_name_is_refused(value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        BOOKING.model_validate_json(_booking(traveller_name=value))
    assert _errors(caught) == [(("traveller_name",), "string_too_short")]


@pytest.mark.parametrize("value", BLANKS, ids=["empty", "spaces", "tab-newline"])
def test_a_blank_line_description_is_refused(value: str) -> None:
    with pytest.raises(ValidationError) as caught:
        REPAIR_INVOICE.model_validate_json(_invoice({**LINE, "description": value}))
    assert _errors(caught) == [(("line_items", 0, "description"), "string_too_short")]


def test_text_is_stripped_and_a_null_name_is_accepted() -> None:
    padded = BOOKING.model_validate_json(_booking(traveller_name=" Anna Keller "))
    assert padded.model_dump()["traveller_name"] == "Anna Keller"
    plain = BOOKING.model_validate_json(_booking(traveller_name="Anna Keller"))
    assert plain.model_dump()["traveller_name"] == "Anna Keller"
    none = BOOKING.model_validate_json(_booking(traveller_name=None))
    assert none.model_dump()["traveller_name"] is None


def _booking_fixture() -> dict[str, Any]:
    path = MOCK / "claim-1-travel" / "booking-confirmation.expected.json"
    return _read(path)


def test_a_fixture_without_a_field_means_null() -> None:
    expected = _booking_fixture()
    expected["fields"] = [f for f in expected["fields"] if f["name"] != "trip_price"]

    booking = BOOKING.model_validate_json(_input_json(expected, BOOKING))
    assert booking.model_dump()["trip_price"] is None


def test_a_fixture_with_an_unknown_name_is_still_refused() -> None:
    expected = _booking_fixture()
    extra = {"name": "iban", "value": "CH93 0076 2011 6238 5295 7"}
    expected["fields"] = [*expected["fields"], extra]

    with pytest.raises(ValidationError) as caught:
        BOOKING.model_validate_json(_input_json(expected, BOOKING))
    assert _errors(caught) == [(("iban",), "extra_forbidden")]


def test_a_fixture_with_a_text_amount_is_still_refused() -> None:
    expected = _booking_fixture()
    for field in expected["fields"]:
        if field["name"] == "trip_price":
            field["value"] = "1840"

    with pytest.raises(ValidationError) as caught:
        BOOKING.model_validate_json(_input_json(expected, BOOKING))
    assert [loc for loc, _ in _errors(caught)] == [("trip_price",)]

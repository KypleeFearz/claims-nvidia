"""The reader bench (issue #82): one kit document read by the active reader and
compared with its expected.json."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.bench import compare
from app.docintel.fixture import FixtureReader
from app.main import create_app
from app.models import Document, Extraction

QUOTE = {"folder": "kit-lukas-household", "file": "repair-quote.pdf"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.delenv("CLAIMS_MODE", raising=False)
    with TestClient(create_app()) as c:
        yield c


def read(client: TestClient, body: dict[str, str]) -> dict[str, Any]:
    res = client.post("/bench/read", json=body)
    assert res.status_code == 200
    out: dict[str, Any] = res.json()
    return out


class Changed(FixtureReader):
    """The fixture reader with faults: a wrong total, a wrong page, one field lost,
    one field that nobody expects, and one line item lost."""

    name = "changed"

    def extract(self, document: Document, data: bytes, workflow: str) -> Extraction:
        out = super().extract(document, data, workflow)
        kept = [f for f in out.fields if f.name != "quote_date"]
        for f in kept:
            if f.name == "total":
                f.value = 1206.0
            if f.name == "customer_name":
                f.source.page = 2
        extra = kept[0].model_copy(update={"name": "vat_number", "value": "CHE-1"})
        return out.model_copy(
            update={"fields": [*kept, extra], "line_items": out.line_items[:-1]}
        )


class Broken(FixtureReader):
    name = "broken"

    def extract(self, document: Document, data: bytes, workflow: str) -> Extraction:
        raise RuntimeError("the model did not answer")


def test_the_fixture_reader_reads_every_kit_document_all_right(
    client: TestClient,
) -> None:
    kit = client.get("/demo/kit").json()
    assert len(kit) == 35
    for row in kit:
        out = read(client, {"folder": row["folder"], "file": row["file"]})
        where = f"{row['folder']}/{row['file']}"
        assert out["reader"] == "fixture" and out["error"] is None, where
        assert out["type"]["right"] and out["type"]["read"] == row["document_type"]
        assert out["counts"]["wrong"] == out["counts"]["missing"] == 0, where
        assert out["counts"]["extra"] == 0 and out["counts"]["right"] >= 1, where
        assert all(f["page_right"] for f in out["fields"]), where
        assert out["line_items"]["right"], where


def test_the_answer_of_one_document(client: TestClient) -> None:
    out = read(client, QUOTE)
    assert out["workflow"] == "property" and out["pages"] == 1
    assert out["seconds"]["classify"] >= 0 and out["seconds"]["extract"] >= 0
    assert out["counts"] == {"right": 5, "wrong": 0, "missing": 0, "extra": 0}
    total = next(f for f in out["fields"] if f["name"] == "total")
    assert total == {
        "name": "total",
        "status": "right",
        "expected": 1260.0,
        "read": 1260.0,
        "expected_page": 1,
        "page": 1,
        "page_right": True,
        "snippet": "CHF 1,260.00",
        "confidence": 1.0,
    }
    assert out["line_items"] == {"expected": 4, "read": 4, "right": True}


def test_a_reader_with_faults_shows_each_fault(client: TestClient) -> None:
    client.app.state.reader = Changed()  # type: ignore[attr-defined]
    out = read(client, QUOTE)
    assert out["reader"] == "changed"
    status = {f["name"]: f["status"] for f in out["fields"]}
    assert status == {
        "quote_number": "right",
        "quote_date": "missing",
        "repair_company": "right",
        "total": "wrong",
        "customer_name": "right",
        "vat_number": "extra",
    }
    assert out["counts"] == {"right": 3, "wrong": 1, "missing": 1, "extra": 1}
    by_name = {f["name"]: f for f in out["fields"]}
    assert by_name["total"]["expected"] == 1260.0 and by_name["total"]["read"] == 1206.0
    lost = by_name["quote_date"]
    assert lost["read"] is None and lost["page"] is None
    assert by_name["customer_name"]["page_right"] is False  # right value, wrong page
    assert by_name["vat_number"]["expected"] is None
    assert out["line_items"] == {"expected": 4, "read": 3, "right": False}


def test_a_reader_that_raises_is_an_error_in_the_answer_not_a_500(
    client: TestClient,
) -> None:
    client.app.state.reader = Broken()  # type: ignore[attr-defined]
    out = read(client, QUOTE)
    assert out["error"] == "RuntimeError: the model did not answer"
    assert out["type"]["read"] == "repair_quote"  # the classification worked
    assert out["counts"] == {"right": 0, "wrong": 0, "missing": 5, "extra": 0}


def test_an_unknown_document_is_a_404_and_the_path_never_comes_from_the_request(
    client: TestClient,
) -> None:
    for body in (
        {"folder": "kit-lukas-household", "file": "nothing.pdf"},
        {"folder": "..", "file": "index.json"},
        {"folder": "kit-lukas-household", "file": "repair-quote.expected.json"},
    ):
        assert client.post("/bench/read", json=body).status_code == 404


def test_compare_numbers_dates_and_text() -> None:
    assert compare(1260.0, "1260") and compare(1260, 1260.004)
    assert not compare(1260.0, 1206.0)
    assert compare("Lukas Meier", " lukas  meier ")  # case and spaces do not count
    assert not compare("Lukas Meier", "Jonas Meier")
    assert compare("2026-09-16", "2026-09-16") and not compare("2026-09-16", None)
    assert compare(True, True) and not compare(True, 1.0)

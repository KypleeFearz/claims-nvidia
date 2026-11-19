"""OmniReader with the real model (issue #18). Skipped in CI.

Run: `cd backend && uv run pytest -m live tests/test_omni_live.py -s`. Reads
NVIDIA_KEY, VLM_BASE_URL and VLM_MODEL from `.env` at the repository root (the shell
wins). Mock data only. `-s` prints each field of claim file 1 next to its expected
value, and the outcome of the claim: the numbers of the pull request.
"""

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.bench import status as field_status
from app.docintel.compare import compare_lines
from app.docintel.omni import OmniReader
from app.main import create_app
from app.models import Document
from tests.live_claims import load_env
from tests.reader_contract import check_reader_contract
from tests.test_travel_api import CLAIM1, PDFS, claim, run_claim

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def omni_env(monkeypatch: pytest.MonkeyPatch) -> None:
    env = load_env()
    for name in ("NVIDIA_KEY", "VLM_BASE_URL", "VLM_MODEL"):
        if name in env and name not in os.environ:
            monkeypatch.setenv(name, env[name])


def test_the_omni_reader_passes_the_reader_contract() -> None:
    check_reader_contract(OmniReader())


def test_claim_file_1_gives_fields_with_sources() -> None:
    reader = OmniReader()
    for name in PDFS:
        path = CLAIM1 / f"{name}.pdf"
        expected = json.loads(path.with_suffix(".expected.json").read_text())
        document = Document(id=f"D-{name}", file=path.name)
        start = time.perf_counter()
        kind = reader.classify(document, path.read_bytes())
        out = reader.extract(document, path.read_bytes(), "travel")
        seconds = time.perf_counter() - start
        read = {f.name: f for f in out.fields}
        print(f"\n{name}: type {kind}, {seconds:.1f} s for 1 page (classify + read)")
        for e in expected["fields"]:
            f = read.get(e["name"])
            status = (
                "missing"
                if f is None
                else field_status(
                    expected["document_type"], e["name"], e["value"], f.value
                )
            )
            where = (
                ""
                if f is None
                else f"page {f.source.page}, {f.confidence}, {f.source.snippet!r}"
            )
            print(f"  {e['name']}: {status} ({f.value if f else None!r}) {where}")
        want = expected.get("line_items", [])
        right = compare_lines(want, out.line_items).all_right
        got = len(out.line_items)
        print(f"  line items: {got} read, {len(want)} expected, right={right}")
        assert out.document_type == expected["document_type"]
        assert out.fields, f"{name}: no field"
        for f in out.fields:
            assert f.source.document_id == document.id and f.source.page == 1
            assert f.source.snippet or f.source.box


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_READER", "omni")
    monkeypatch.setenv("CLAIMS_MODE", "local")
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def test_claim_file_1_through_the_api_with_the_omni_reader(client: TestClient) -> None:
    """The outcome is printed, not asserted: a reading error changes it, and the
    routing is right for the fields it gets. Expected: straight_through."""
    c = claim(client, run_claim(client))
    print(f"\noutcome: {c['outcome']}, score {c['score']}")
    print(f"reasons: {c['outcome_reasons']}")
    print(f"signals: {[s['code'] for s in c['signals']]}")
    for d in c["documents"]:
        print(f"{d['file']}: {d['document_type']}, {len(d['fields'])} fields")
        for f in d["fields"]:
            assert f["source"]["document_id"] == d["id"]
            assert f["source"]["snippet"] or f["source"]["box"]
    assert len(c["documents"]) == 3

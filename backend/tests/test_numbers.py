"""Business numbers (issue #96): the claims of the demo, and the kit played through
the active reader."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.docintel.fixture import FixtureReader
from app.handler import HANDLERS
from app.main import create_app
from app.models import Document, Extraction
from tests.test_handler import refuse_claim, review_claim
from tests.test_history import elena
from tests.test_payment import claim_file_1

SOFIA, DAVID = HANDLERS


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def test_no_claim_gives_zeros_and_no_share(client: TestClient) -> None:
    out = client.get("/numbers").json()
    assert out["claims"] == 0 and out["by_status"] == {}
    assert out["straight_through_share"] is None
    assert out["median_minutes_to_end"] is None and out["paid"] == 0


def test_the_numbers_of_four_claims(client: TestClient) -> None:
    claim_file_1(client)  # paid alone: 1,500
    approved = review_claim(client)  # 3,200 claimed, 3,000 proposed
    client.post(
        f"/claims/{approved}/decisions", json={"action": "approve", "by": SOFIA}
    )
    refused = review_claim(client)
    refuse_claim(client, refused)
    siu = elena(client)  # investigation: 1,750 claimed and proposed
    for task in ("T1", "T2"):
        change = {"by": DAVID, "done": True, "note": "ok"}
        client.post(f"/claims/{siu}/investigation/tasks/{task}", json=change)
    conclude = {"action": "clear", "by": DAVID, "reason": "A family link."}
    client.post(f"/claims/{siu}/decisions", json=conclude)
    client.post("/claims", json={"customer_id": "C-1001"})  # still in the chat

    out = client.get("/numbers").json()
    assert out["claims"] == 5 and out["routed"] == 4
    assert out["by_status"] == {"open": 1, "in_review": 1, "refused": 1, "paid": 2}
    assert out["straight_through"] == 1 and out["to_a_person"] == 3
    assert out["straight_through_share"] == 0.25
    assert out["claimed"] == 1500 + 3200 + 3200 + 1750
    assert out["proposed"] == 1500 + 3000 + 3000 + 1750
    assert out["paid"] == 1500 + 3000
    assert out["open_reserve"] == 1750  # the claim of Elena waits for the handler
    assert out["ended"] == 3 and out["median_minutes_to_end"] >= 0
    assert out["investigations"] == {
        "open": 0,
        "confirmed": 0,
        "not_confirmed": 1,
        "not_confirmed_share": 1.0,
    }
    assert out["decisions"] == {
        "approve": 1,
        "propose_refusal": 1,
        "refuse": 1,
        "clear": 1,
    }


def test_the_kit_is_right_with_the_fixture_reader_and_leaves_no_trace(
    client: TestClient, tmp_path: Path
) -> None:
    out = client.post("/numbers/kit").json()
    assert out["reader"] == "fixture" and out["stories"] == 11
    assert out["outcomes_right"] == out["signals_right"] == out["amounts_right"] == 11
    assert out["seeded_fraud"] == 3 and out["fraud_caught"] == 3
    assert out["fraud_missed"] == 0 and out["false_alarms"] == 0
    row = next(r for r in out["rows"] if r["story"] == "claim-3-health/4450")
    assert row["outcome"] == "investigation" and row["payable"] == 1305.0
    assert row["signals"] == ["C001", "R003", "R004", "R005", "T001", "T002", "T003"]
    assert all(r["error"] is None for r in out["rows"])
    # a store of its own: no claim in the queue, no export file, no upload
    assert client.get("/claims").json() == []
    assert not (tmp_path / "exports").exists()
    assert not (tmp_path / "uploads").exists()


class WrongTotal(FixtureReader):
    """Reads the total of every medical invoice as 840: the lines of the invoice of
    claim file 3 (1,450) then seem wrong in another way, and the amount changes."""

    name = "wrong-total"

    def extract(self, document: Document, data: bytes, workflow: str) -> Extraction:
        out = super().extract(document, data, workflow)
        if out.document_type == "medical_invoice":
            for f in out.fields:
                if f.name == "total":
                    f.value = 1450.0  # the edited total of 4,450 is not seen
        return out


def test_a_reader_that_misses_an_edited_total_shows_the_story_that_moved(
    client: TestClient,
) -> None:
    client.app.state.reader = WrongTotal()  # type: ignore[attr-defined]
    out = client.post("/numbers/kit").json()
    assert out["reader"] == "wrong-total"
    moved = [r["story"] for r in out["rows"] if not r["signals_right"]]
    assert "claim-3-health/4450" in moved
    row = next(r for r in out["rows"] if r["story"] == "claim-3-health/4450")
    assert "T003" in row["expected_signals"] and "T003" not in row["signals"]
    assert out["signals_right"] < 11


class Broken(FixtureReader):
    name = "broken"

    def extract(self, document: Document, data: bytes, workflow: str) -> Extraction:
        raise RuntimeError("the model did not answer")


def test_a_reader_that_raises_gives_rows_with_an_error_not_a_500(
    client: TestClient,
) -> None:
    client.app.state.reader = Broken()  # type: ignore[attr-defined]
    res = client.post("/numbers/kit")
    assert res.status_code == 200
    out: dict[str, Any] = res.json()
    assert out["outcomes_right"] == 0 and out["fraud_missed"] == 3
    assert all("the model did not answer" in r["error"] for r in out["rows"])

"""One claim, several requests at the same time: no write is lost."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import Store
from tests.builders import document
from tests.reader_contract import MOCK

CLAIM1 = MOCK / "claim-1-travel"
PDFS = ["booking-confirmation", "cancellation-confirmation", "medical-certificate"]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def test_three_uploads_at_the_same_time_keep_three_documents(
    client: TestClient,
) -> None:
    for _ in range(5):  # the race is rare: try several claims
        cid = str(
            client.post("/claims", json={"customer_id": "C-1001"}).json()["claim_id"]
        )
        client.put(f"/claims/{cid}/policy", json={"policy_number": "TRV-2026-0417"})

        def up(name: str, cid: str = cid) -> int:
            data = (CLAIM1 / f"{name}.pdf").read_bytes()
            res = client.post(f"/claims/{cid}/documents", files={"file": (name, data)})
            return res.status_code

        with ThreadPoolExecutor(3) as pool:
            assert list(pool.map(up, PDFS)) == [201, 201, 201]
        docs = client.get(f"/claims/{cid}").json()["documents"]
        assert sorted(d["document_type"] for d in docs) == [
            "booking_confirmation",
            "cancellation_confirmation",
            "medical_certificate",
        ]


def test_store_read_modify_write_is_atomic(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "claims.db"))
    claim = store.create_claim("C-1001")
    assert claim is not None

    def add(i: int) -> None:
        store.save_document(claim.id, document(f"D-{i}", "receipt", {"total": i}))
        store.update_claim(claim.id, {"description": f"d{i}"})

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(add, range(60)))
    saved = store.get_claim(claim.id)
    assert saved is not None
    assert sorted(d.id for d in saved.documents) == sorted(f"D-{i}" for i in range(60))


def test_many_visitors_open_a_claim_and_pick_a_policy_at_the_same_time(
    client: TestClient,
) -> None:
    """Issue #115: the reads of the customers and of the policies share one SQLite
    connection. Without the lock, two requests at once read each other's rows and
    one of them is a 500."""

    def visitor(i: int) -> tuple[int, int, int]:
        customer = ("C-1001", "C-1002", "C-1003", "C-1004")[i % 4]
        made = client.post("/claims", json={"customer_id": customer})
        if made.status_code != 201:
            return made.status_code, 0, 0
        cid = made.json()["claim_id"]
        return (
            made.status_code,
            client.get(f"/claims/{cid}/policies").status_code,
            client.get("/customers").status_code,
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(visitor, range(64)))
    assert set(results) == {(201, 200, 200)}
    assert len(client.get("/claims").json()) == 64

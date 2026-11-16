"""Signals R006 to R010 (issue #90): the history of the customer and the links
between persons, read from data/mock/history.json."""

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.checks import history
from app.checks.history import check_history, earlier_claims, links
from app.main import create_app
from app.models import Claim, Customer
from tests.builders import document, travel_claim
from tests.reader_contract import MOCK


def claim_of(customer_id: str, name: str = "x", **more: Any) -> Claim:
    return travel_claim(customer=Customer(id=customer_id, name=name), **more)


def codes(claim: Claim) -> list[str]:
    return [s.code for s in check_history(claim)]


def test_the_three_first_customers_have_a_quiet_history() -> None:
    for cid in ("C-1001", "C-1002", "C-1003"):
        assert codes(claim_of(cid)) == [], cid
        assert len(earlier_claims(cid)) == 1 and links(cid) == []


def test_a_shared_account_and_phone_with_a_person_known_for_fraud() -> None:
    found = {s.code: s for s in check_history(claim_of("C-1004", "Elena Rossi"))}
    assert list(found) == ["R008", "R009"]
    assert sum(s.points for s in found.values()) == 60
    assert "Reto Vogel (X-9001)" in found["R008"].detail
    assert "H-2025-1120" in found["R008"].detail  # his claims closed as fraud
    assert found["R009"].detail.startswith("The customer has the phone of Reto Vogel")
    assert all(s.category == "rule" and s.evidence == [] for s in found.values())
    [link] = links("C-1004")
    assert link["shared"] == ["account", "phone"] and len(link["fraud_claims"]) == 2


def test_an_earlier_claim_closed_as_fraud_and_a_customer_nobody_knows() -> None:
    # Reto shares his account with Elena, who has no claim closed as fraud: no R009
    assert codes(claim_of("X-9001")) == ["R007", "R008"]
    assert codes(claim_of("C-0000")) == []  # no history at all: no signal


def test_a_provider_with_claims_closed_as_fraud_points_to_the_page() -> None:
    doc = document(
        "D-inv", "repair_invoice", {"repair_company": "sanitaer  blitz gmbh"}
    )
    [s] = check_history(claim_of("C-1002", documents=[doc]))
    assert s.code == "R010" and "H-2026-0214, H-2026-0330" in s.detail
    assert s.evidence[0].document_id == "D-inv" and s.evidence[0].page == 1
    clean = document("D-inv", "repair_invoice", {"repair_company": "Blaubach AG"})
    assert codes(claim_of("C-1002", documents=[clean])) == []


def test_three_claims_in_twelve_months(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = json.loads((MOCK / "history.json").read_text())
    for i, day in enumerate(("2025-09-01", "2026-01-15", "2026-07-30", "2024-01-01")):
        data["claims"].append(
            {
                "id": f"H-test-{i}",
                "person": "C-1001",
                "date": day,
                "line": "Travel",
                "cause": "luggage",
                "amount": 100.0,
                "result": "paid",
                "provider": None,
            }
        )
    (tmp_path / "mock").mkdir()
    (tmp_path / "mock" / "history.json").write_text(json.dumps(data))
    monkeypatch.setenv("CLAIMS_DATA", str(tmp_path))
    history._load.cache_clear()
    # the incident is on 3 August 2026: three claims are in the 12 months before it
    # (the one of July 2025 is too old, the one of 2024 too)
    [s] = check_history(claim_of("C-1001", incident_date=date(2026, 8, 3)))
    assert s.code == "R006" and s.detail.startswith("3 earlier claims in 12 months")
    assert codes(claim_of("C-1001", incident_date=date(2025, 10, 1))) == []
    assert codes(claim_of("C-1001", incident_date=None)) == []


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.delenv("CLAIMS_MODE", raising=False)
    with TestClient(create_app()) as c:
        yield c


def elena(client: TestClient) -> str:
    cid = str(client.post("/claims", json={"customer_id": "C-1004"}).json()["claim_id"])
    client.put(f"/claims/{cid}/policy", json={"policy_number": "TRV-2026-0850"})
    story = (
        "I had to cancel my trip to Lisbon on 2 June 2026 because of a family "
        "emergency. I claim CHF 1,750."
    )
    client.post(f"/claims/{cid}/messages", json={"text": story})
    for name in ("booking-confirmation.pdf", "cancellation-confirmation.pdf"):
        data = (MOCK / "kit-elena-travel" / name).read_bytes()
        client.post(f"/claims/{cid}/documents", files={"file": (name, data)})
    client.post(f"/claims/{cid}/messages", json={"text": "done"})
    return cid


def test_clean_documents_and_an_investigation_because_of_the_links(
    client: TestClient,
) -> None:
    cid = elena(client)
    c = client.get(f"/claims/{cid}").json()
    assert sorted(s["code"] for s in c["signals"]) == ["R008", "R009"]
    assert c["score"] == 60 and c["outcome"] == "investigation"
    assert c["status"] == "in_investigation"
    report = c["fraud_report"]
    assert "payout account is also the account of another person" in report["summary"]
    assert report["next_steps"][-1].startswith("Read the earlier claims and the linked")
    # the settlement is clean: the documents prove the claimed amount
    assert c["proposal"]["payable"] == 1750.0 and c["proposal"]["needs_person"] == []


def test_the_customer_reads_nothing_of_the_history_or_the_links(
    client: TestClient,
) -> None:
    cid = elena(client)
    seen = (
        client.get(f"/claims/{cid}/customer").text
        + client.get(f"/claims/{cid}/messages").text
    )
    for word in ("Reto", "Vogel", "X-9001", "MOCK-ACCT", "account", "fraud", "R008"):
        assert word not in seen, word


def test_the_history_of_a_claim_for_the_handler(client: TestClient) -> None:
    cid = elena(client)
    out = client.get(f"/claims/{cid}/history").json()
    assert out["person"]["account"] == "MOCK-ACCT-9001"
    assert out["earlier_claims"] == []
    assert [x["name"] for x in out["links"]] == ["Reto Vogel"]
    assert out["links"][0]["claims"][0]["result"] == "fraud"
    assert client.get("/claims/CL-none/history").status_code == 404

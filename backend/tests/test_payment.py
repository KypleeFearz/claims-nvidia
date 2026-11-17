"""Reserve, payee check, mock payment and export (issue #94, package B7)."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.fraud.routing import route
from app.handler import HANDLERS
from app.main import create_app
from app.models import Customer, Outcome
from app.payment import ExportRecord, payee_problem
from app.workflows import WORKFLOWS
from tests.builders import travel_claim
from tests.reader_contract import MOCK
from tests.test_handler import refuse_claim, review_claim

SOFIA = HANDLERS[0]
TRIP = (
    "My trip was cancelled because I was ill. "
    "It began on 3 August 2026 and I claim CHF 1,500."
)
FILE_1 = ("booking-confirmation", "cancellation-confirmation", "medical-certificate")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def claim(client: TestClient, cid: str) -> dict[str, Any]:
    out: dict[str, Any] = client.get(f"/claims/{cid}").json()
    return out


def claim_file_1(client: TestClient) -> str:
    cid = str(client.post("/claims", json={"customer_id": "C-1001"}).json()["claim_id"])
    client.put(f"/claims/{cid}/policy", json={"policy_number": "TRV-2026-0417"})
    client.post(f"/claims/{cid}/messages", json={"text": TRIP})
    for name in FILE_1:
        data = (MOCK / "claim-1-travel" / f"{name}.pdf").read_bytes()
        client.post(f"/claims/{cid}/documents", files={"file": (f"{name}.pdf", data)})
    client.post(f"/claims/{cid}/messages", json={"text": "done"})
    return cid


def events(client: TestClient, cid: str, stage: str) -> list[Any]:
    store = client.app.state.store  # type: ignore[attr-defined]
    return [e for e in store.events_since(cid, 0) if e.stage == stage]


def test_claim_file_1_is_paid_and_exported_with_no_person_in_the_loop(
    client: TestClient, tmp_path: Path
) -> None:
    cid = claim_file_1(client)
    c = claim(client, cid)
    assert c["status"] == "paid" and c["outcome"] == "straight_through"
    assert c["decisions"] == []  # nobody decided: the contract alone
    assert (
        c["payment"]["amount"] == 1500.0 and c["payment"]["reference"] == f"PAY-{cid}"
    )
    assert c["payment"]["account"] == "MOCK-ACCT-1001"
    moves = [(e.inputs["from"], e.inputs["to"]) for e in events(client, cid, "status")]
    assert moves == [("open", "validated"), ("validated", "paid")]
    # the export: one file, valid against the schema, every field with its page
    file = tmp_path / "exports" / f"{cid}.json"
    record = ExportRecord.model_validate_json(file.read_text())
    assert record.paid_amount == 1500.0 and record.decided_by is None
    assert record.policy_number == "TRV-2026-0417" and record.signals == []
    assert record.fields and all(f["page"] >= 1 for f in record.fields)
    assert client.get(f"/claims/{cid}/export").json() == json.loads(file.read_text())
    [exported] = events(client, cid, "export")
    assert exported.outputs["amount"] == 1500.0


def test_the_reserve_follows_the_claim(client: TestClient) -> None:
    cid = review_claim(client)  # claim file 2: 3,200 claimed, 3,000 proposed
    assert claim(client, cid)["reserve"] == 3000.0
    reserves = [e.outputs["reserve"] for e in events(client, cid, "reserve")]
    assert reserves == [3200.0, 3000.0]  # first notice, then the routing
    body = {"action": "approve", "by": SOFIA, "amount": 2500, "reason": "Old cabinets."}
    client.post(f"/claims/{cid}/decisions", json=body)
    reserves = [e.outputs["reserve"] for e in events(client, cid, "reserve")]
    assert reserves == [3200.0, 3000.0, 2500.0, 0.0]  # approved, then paid
    assert claim(client, cid)["reserve"] == 0.0


def test_an_approval_is_paid_at_once_and_the_export_names_the_handler(
    client: TestClient, tmp_path: Path
) -> None:
    cid = review_claim(client)
    res = client.post(
        f"/claims/{cid}/decisions", json={"action": "approve", "by": SOFIA}
    )
    assert res.status_code == 200 and res.json()["status"] == "paid"
    assert res.json()["payment"]["amount"] == 3000.0
    record = ExportRecord.model_validate_json(
        (tmp_path / "exports" / f"{cid}.json").read_text()
    )
    assert record.decided_by == SOFIA and record.outcome == "handler_review"
    # paid is the end: no other decision
    again = {"action": "refuse", "by": SOFIA, "reason": "x"}
    assert client.post(f"/claims/{cid}/decisions", json=again).status_code == 409


def test_a_refusal_frees_the_reserve_and_pays_and_exports_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    cid = review_claim(client)
    assert refuse_claim(client, cid).json()["status"] == "refused"
    c = claim(client, cid)
    assert c["reserve"] == 0.0 and c["payment"] is None
    assert not (tmp_path / "exports" / f"{cid}.json").exists()
    assert client.get(f"/claims/{cid}/export").status_code == 404


def test_an_approval_of_zero_pays_nothing(client: TestClient) -> None:
    cid = review_claim(client)
    body = {
        "action": "approve",
        "by": SOFIA,
        "amount": 0,
        "reason": "Below the deductible.",
    }
    assert (
        client.post(f"/claims/{cid}/decisions", json=body).json()["status"]
        == "approved"
    )
    assert claim(client, cid)["payment"] is None


def test_the_customer_reads_that_it_is_paid_and_never_the_reserve(
    client: TestClient,
) -> None:
    cid = claim_file_1(client)
    view = client.get(f"/claims/{cid}/customer").json()
    assert view["status"] == "paid"
    assert view["status_line"] == "Paid: CHF 1,500.00 to your account ending 1001."
    assert view["letter"]["amount"] == 1500.0
    text = json.dumps(view) + client.get(f"/claims/{cid}/messages").text
    assert "reserve" not in text.lower() and "MOCK-ACCT" not in text


def test_the_system_does_not_pay_alone_to_an_account_it_cannot_confirm() -> None:
    assert payee_problem(travel_claim()) is None  # Anna: her own account
    travel = WORKFLOWS["travel"]
    assert route(travel_claim(), [], travel)[0] == Outcome.straight_through
    for customer, why in (
        ("C-1004", "also the account of another person"),  # Elena and Reto
        ("C-0000", "No payout account is known"),
    ):
        claim = travel_claim(customer=Customer(id=customer, name="x"))
        assert why in (payee_problem(claim) or "")
        outcome, reasons = route(claim, [], travel)
        assert outcome == Outcome.handler_review
        assert any(r.startswith("Payment: ") and why in r for r in reasons)


def test_the_reset_deletes_the_export_files(client: TestClient, tmp_path: Path) -> None:
    cid = claim_file_1(client)
    assert (tmp_path / "exports" / f"{cid}.json").exists()
    assert client.post("/demo/reset").status_code == 200
    assert not (tmp_path / "exports").exists()

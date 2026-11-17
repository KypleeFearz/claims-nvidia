"""Experts and third parties (issue #102, package B8): an expert request and a
recovery. `local` mode, through the HTTP API."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.fraud.routing import route
from app.handler import HANDLERS
from app.main import create_app
from app.models import Outcome
from app.workflows import WORKFLOWS
from tests.builders import document, travel_claim
from tests.test_handler import refuse_claim, review_claim
from tests.test_payment import claim_file_1

SOFIA, DAVID = HANDLERS


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def claim(client: TestClient, cid: str) -> dict[str, Any]:
    out: dict[str, Any] = client.get(f"/claims/{cid}").json()
    return out


def post(client: TestClient, cid: str, path: str, **body: Any) -> Any:
    return client.post(f"/claims/{cid}/{path}", json={"by": SOFIA, **body})


def events(client: TestClient, cid: str, stage: str) -> list[Any]:
    store = client.app.state.store  # type: ignore[attr-defined]
    return [e for e in store.events_since(cid, 0) if e.stage == stage]


def customer_side(client: TestClient, cid: str) -> tuple[Any, Any]:
    return (
        client.get(f"/claims/{cid}/customer").json(),
        client.get(f"/claims/{cid}/messages").json(),
    )


# ---- the expert request -----------------------------------------------------------


def test_a_large_loss_goes_to_a_handler_with_the_reason_needs_an_expert() -> None:
    prop = WORKFLOWS["property"]
    assert prop.expert_above == 5000 and WORKFLOWS["health"].expert_above == 3000
    assert WORKFLOWS["travel"].expert_above is None
    docs = [document("D-inv", "repair_invoice", {"total": 6200.0})]
    big = travel_claim(
        type="property", cause="water", claimed_amount=6200, documents=docs
    )
    outcome, reasons = route(big, [], prop)
    assert outcome == Outcome.handler_review
    assert "A loss above CHF 5,000 needs an expert." in reasons
    small = travel_claim(
        type="property", cause="water", claimed_amount=5000, documents=docs
    )
    assert not any("expert" in r for r in route(small, [], prop)[1])


def test_a_handler_asks_an_expert_and_cannot_decide_before_the_report(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    before = customer_side(client, cid)
    assert post(client, cid, "experts", question="  ").status_code == 422
    res = post(client, cid, "experts", question="Is CHF 3,200 right for this floor?")
    assert res.status_code == 200
    [request] = res.json()["experts"]
    assert request["id"] == "E1" and request["kind"] == "loss adjuster"
    assert request["asked_by"] == SOFIA and request["report"] is None
    assert claim(client, cid)["status"] == "in_review"  # the status does not change
    # no decision while the expert has the file
    for action in ("approve", "refuse"):
        res = post(client, cid, "decisions", action=action, reason="x")
        assert res.status_code == 409 and "expert" in res.json()["detail"], action
    [asked] = events(client, cid, "expert")
    assert asked.actor == "handler" and asked.inputs == {"request": "E1", "by": SOFIA}
    assert customer_side(client, cid) == before  # non-negotiable 2


def test_the_report_comes_back_and_the_handler_approves_the_assessed_amount(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    post(client, cid, "experts", question="Is CHF 3,200 right for this floor?")
    report = {
        "expert": "Atelier Expertise Lugano",
        "finding": "The floor is 12 years old. The fair cost is CHF 2,700.",
        "amount": 2700,
    }
    assert post(client, cid, "experts/E9/report", **report).status_code == 404
    assert (
        post(client, cid, "experts/E1/report", **{**report, "finding": ""}).status_code
        == 422
    )
    res = post(client, cid, "experts/E1/report", **report)
    assert res.status_code == 200
    got = res.json()["experts"][0]["report"]
    assert got["amount"] == 2700 and got["expert"] == "Atelier Expertise Lugano"
    assert post(client, cid, "experts/E1/report", **report).status_code == 409  # once
    # the handler decides: the amount of the expert, with a reason (not the proposal)
    body = {"action": "approve", "amount": 2700, "reason": "Report of the expert."}
    res = post(client, cid, "decisions", **body)
    assert res.status_code == 200 and res.json()["payment"]["amount"] == 2700
    assert len(events(client, cid, "expert")) == 2  # the request, then the report


def test_a_medical_adviser_for_a_health_claim_and_no_expert_on_a_closed_claim(
    client: TestClient,
) -> None:
    from tests.test_history import elena

    cid = elena(client)  # a travel claim: no special kind
    post(client, cid, "experts", question="Check the booking.")
    assert claim(client, cid)["experts"][0]["kind"] == "expert"
    paid = claim_file_1(client)
    assert post(client, paid, "experts", question="x").status_code == 409
    bad = client.post(f"/claims/{cid}/experts", json={"by": "Nobody", "question": "x"})
    assert bad.status_code == 422


# ---- the recovery -----------------------------------------------------------------


def test_a_recovery_waits_for_the_payment_then_is_open_with_the_paid_amount(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    before = customer_side(client, cid)
    assert post(client, cid, "recovery", party="", reason="x").status_code == 422
    res = post(
        client,
        cid,
        "recovery",
        party="Neighbour, flat 3B",
        reason="The pipe that broke is his.",
    )
    assert res.status_code == 200
    rec = res.json()["recovery"]
    assert rec["status"] == "planned" and rec["amount"] is None
    assert rec["party"] == "Neighbour, flat 3B" and rec["opened_by"] == SOFIA
    assert customer_side(client, cid) == before  # the customer reads nothing of it
    assert (
        post(client, cid, "recovery", party="x", reason="y").status_code == 409
    )  # one
    post(client, cid, "decisions", action="approve")
    rec = claim(client, cid)["recovery"]
    assert rec["status"] == "open" and rec["amount"] == 3000.0  # what was paid
    stages = [e.title for e in events(client, cid, "recovery")]
    assert stages[0].startswith("Recovery planned") and stages[1].startswith(
        "Recovery open"
    )


def test_a_recovery_on_a_paid_claim_is_open_at_once_and_ends_recovered(
    client: TestClient,
) -> None:
    cid = claim_file_1(client)  # paid: 1,500
    res = post(
        client, cid, "recovery", party="Airline", reason="The flight was theirs."
    )
    assert res.json()["recovery"]["status"] == "open"
    assert res.json()["recovery"]["amount"] == 1500.0
    assert post(client, cid, "recovery/result", result="recovered").status_code == 422
    res = post(client, cid, "recovery/result", result="recovered", amount=1200)
    assert res.status_code == 200
    rec = res.json()["recovery"]
    assert rec["status"] == "recovered" and rec["recovered"] == 1200
    assert (
        post(client, cid, "recovery/result", result="abandoned", reason="x").status_code
        == 409
    )
    assert claim(client, cid)["status"] == "paid"  # the claim itself does not move


def test_a_recovery_can_be_abandoned_with_a_reason_and_not_before_the_payment(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    post(client, cid, "recovery", party="Neighbour", reason="His pipe.")
    early = post(client, cid, "recovery/result", result="abandoned", reason="x")
    assert early.status_code == 409  # nothing is paid yet: nothing to recover
    post(client, cid, "decisions", action="approve")
    assert post(client, cid, "recovery/result", result="abandoned").status_code == 422
    res = post(client, cid, "recovery/result", result="abandoned", reason="No proof.")
    assert res.json()["recovery"]["status"] == "abandoned"
    assert res.json()["recovery"]["recovered"] == 0
    none = claim_file_1(client)
    assert (
        post(
            client, none, "recovery/result", result="abandoned", reason="x"
        ).status_code
        == 409
    )


def test_a_refused_claim_has_no_recovery(client: TestClient) -> None:
    cid = review_claim(client)
    assert refuse_claim(client, cid).json()["status"] == "refused"
    res = post(client, cid, "recovery", party="Neighbour", reason="His pipe.")
    assert res.status_code == 409


def test_the_numbers_count_the_experts_and_the_recoveries(client: TestClient) -> None:
    paid = claim_file_1(client)
    post(client, paid, "recovery", party="Airline", reason="Their flight.")
    post(client, paid, "recovery/result", result="recovered", amount=1200)
    other = claim_file_1(client)
    post(client, other, "recovery", party="Airline", reason="Their flight.")
    waits = review_claim(client)
    post(client, waits, "experts", question="Is the cost right?")
    out = client.get("/numbers").json()
    assert out["experts_open"] == 1
    assert out["recoveries"] == {
        "open": 1,
        "open_amount": 1500.0,
        "recovered": 1,
        "recovered_amount": 1200.0,
    }

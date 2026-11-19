"""The settlement proposal (issue #86): the 10 kit stories against the calculation
by hand of data/mock/settlements.json, and each rule alone."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Claim, Policy
from app.settlement import propose
from tests.builders import TRAVEL_POLICY, document, travel_claim, travel_documents
from tests.reader_contract import MOCK

INDEX: dict[str, Any] = json.loads((MOCK / "index.json").read_text())
BY_HAND: dict[str, Any] = json.loads((MOCK / "settlements.json").read_text())
STORIES = [(s, t) for s in INDEX["scenarios"] for t in s["stories"]]
IDS = [f"{s['folder']}/{t['amount']:g}" for s, t in STORIES]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.delenv("CLAIMS_MODE", raising=False)
    with TestClient(create_app()) as c:
        yield c


def test_every_kit_story_has_a_calculation_by_hand() -> None:
    assert sorted(IDS) == sorted(k for k in BY_HAND if not k.startswith("_"))
    assert len(IDS) == 11


@pytest.mark.parametrize(("scenario", "story"), STORIES, ids=IDS)
def test_the_proposal_of_a_kit_story_is_right_to_the_cent(
    client: TestClient, scenario: dict[str, Any], story: dict[str, Any]
) -> None:
    cid = client.post("/claims", json={"customer_id": scenario["customer_id"]}).json()[
        "claim_id"
    ]
    client.put(f"/claims/{cid}/policy", json={"policy_number": scenario["policy"]})
    client.post(f"/claims/{cid}/messages", json={"text": story["text"]})
    for doc in scenario["documents"]:
        data = (MOCK / scenario["folder"] / doc["file"]).read_bytes()
        client.post(f"/claims/{cid}/documents", files={"file": (doc["file"], data)})
    client.post(f"/claims/{cid}/messages", json={"text": "done"})
    claim = client.get(f"/claims/{cid}").json()
    hand = BY_HAND[f"{scenario['folder']}/{story['amount']:g}"]
    proposal = claim["proposal"]
    assert proposal["payable"] == hand["payable"], hand["calculation"]
    assert bool(proposal["needs_person"]) == hand["needs_person"]
    # the lines add up to the amount, and every line names its article
    assert round(sum(x["amount"] for x in proposal["lines"]), 2) == hand["payable"]
    assert all(x["article"].startswith("Art. ") for x in proposal["lines"])
    assert proposal["lines"][0]["sources"], "the loss has no source page"
    # the outcome of the kit does not change (tests/test_document_kit.py checks it),
    # and the system never pays alone what a person must decide
    assert claim["outcome"] == story["outcome"]
    if proposal["needs_person"]:
        assert claim["outcome"] != "straight_through"


def policy(**terms: Any) -> Policy:
    return TRAVEL_POLICY.model_copy(update=terms)


def claim_with(policy: Policy, **more: Any) -> Claim:
    return travel_claim(policy=policy, documents=travel_documents(), **more)


def labels(claim: Claim) -> list[tuple[str, float]]:
    out = propose(claim)
    assert out is not None
    return [(x.label, x.amount) for x in out.lines]


def test_the_loss_less_the_deductible_is_paid_with_no_person() -> None:
    out = propose(claim_with(policy(deductible=150), claimed_amount=1500))
    assert out is not None and out.payable == 1350.0 and out.needs_person == []
    assert labels(claim_with(policy(deductible=150), claimed_amount=1500)) == [
        ("Documented loss", 1500.0),
        ("Deductible", -150.0),
    ]
    assert out.lines[0].sources and out.lines[0].article.startswith("Art. 3.1")


def test_a_claim_below_the_documents_pays_the_claimed_amount() -> None:
    out = propose(claim_with(policy(), claimed_amount=1000))
    assert out is not None and out.payable == 1000.0 and out.needs_person == []
    assert labels(claim_with(policy(), claimed_amount=1000))[1] == (
        "Not claimed",
        -500.0,
    )


def test_a_claim_above_the_documents_pays_the_documents_and_needs_a_person() -> None:
    out = propose(claim_with(policy(), claimed_amount=1840))
    assert out is not None and out.payable == 1500.0
    assert out.needs_person == ["The claimed amount is above what the documents prove."]


def test_a_cause_that_is_not_covered_gives_zero_and_needs_a_person() -> None:
    out = propose(
        claim_with(
            policy(covered_causes=["luggage"]), claimed_amount=1500, cause="illness"
        )
    )
    assert out is not None and out.payable == 0.0
    assert out.lines[-1].label == "Cause not covered" and out.lines[-1].amount == -1500
    assert "not a covered cause" in out.needs_person[0]


def test_the_share_of_the_customer_comes_before_the_deductible() -> None:
    terms = policy(coinsurance=0.1, deductible=100)
    assert labels(claim_with(terms, claimed_amount=1500)) == [
        ("Documented loss", 1500.0),
        ("Share of the customer", -150.0),
        ("Deductible", -100.0),
    ]


def test_a_deductible_above_the_loss_gives_zero_and_a_person() -> None:
    out = propose(claim_with(policy(deductible=5000), claimed_amount=1500))
    assert out is not None and out.payable == 0.0
    assert out.needs_person == ["The proposal is CHF 0."]


def test_the_coverage_limit_caps_the_amount_and_needs_a_person() -> None:
    out = propose(claim_with(policy(coverage_limit=1000), claimed_amount=1500))
    assert out is not None and out.payable == 1000.0
    assert out.needs_person == ["The amount is above the coverage limit."]


def test_no_document_with_an_amount_gives_no_amount_and_a_person() -> None:
    docs = [document("D-med", "medical_certificate", {"patient_name": "Anna Keller"})]
    out = propose(travel_claim(documents=docs, claimed_amount=1500))
    assert out is not None and out.payable is None and out.lines == []
    assert out.needs_person == ["No document gives the amount of the loss."]


def test_no_policy_no_proposal_and_no_claimed_amount_pays_the_documents() -> None:
    assert propose(travel_claim(policy=None)) is None
    out = propose(claim_with(policy(), claimed_amount=None))
    assert out is not None and out.payable == 1500.0 and out.needs_person == []


def test_a_trip_with_no_refund_read_is_not_paid_with_no_person() -> None:
    # a real reader can miss the field (pull request #122): the refund then counts
    # as 0, and that is a guess that the system must not pay alone
    docs = travel_documents()
    docs[1] = document(
        "D-cancel", "cancellation_confirmation", {"cancellation_date": "2026-08-04"}
    )
    proposal = propose(travel_claim(documents=docs, claimed_amount=1840.0))
    assert proposal is not None
    assert proposal.payable == 1840.0 - TRAVEL_POLICY.deductible
    assert proposal.needs_person == ["No document gives the refund of the trip."]
    # with the refund read, the same claim needs no person for this reason
    read = propose(travel_claim(claimed_amount=1500.0))
    assert read is not None and read.needs_person == []

"""The decisions of a handler (issue #84, package B3): approve, refuse, ask the
customer, send to investigation, clear. `local` mode, through the HTTP API."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.handler import APPROVED, DECIDED, HANDLERS
from app.main import create_app
from app.views import TO_A_PERSON
from tests.reader_contract import MOCK

SOFIA, DAVID = HANDLERS
PAID = APPROVED.format(amount=3000)
WATER = "Water damage in the kitchen on 14 September 2026. I claim CHF 3,200."


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.delenv("CLAIMS_MODE", raising=False)
    with TestClient(create_app()) as c:
        yield c


def upload(client: TestClient, cid: str, path: Path) -> int:
    res = client.post(
        f"/claims/{cid}/documents", files={"file": (path.name, path.read_bytes())}
    )
    return res.status_code


def review_claim(client: TestClient) -> str:
    """Claim file 2: clean, but above the limit, so a handler reviews it."""
    cid = str(client.post("/claims", json={"customer_id": "C-1002"}).json()["claim_id"])
    client.put(f"/claims/{cid}/policy", json={"policy_number": "HH-2024-0933"})
    client.post(f"/claims/{cid}/messages", json={"text": WATER})
    for name in ("repair-invoice.pdf", "damage-photo-1.jpg", "damage-photo-2.jpg"):
        assert upload(client, cid, MOCK / "claim-2-property" / name) == 201
    client.post(f"/claims/{cid}/messages", json={"text": "done"})
    assert claim(client, cid)["status"] == "in_review"
    return cid


def claim(client: TestClient, cid: str) -> dict[str, Any]:
    out: dict[str, Any] = client.get(f"/claims/{cid}").json()
    return out


def decide(client: TestClient, cid: str, action: str, **more: str) -> Any:
    body = {"action": action, "by": SOFIA, **more}
    return client.post(f"/claims/{cid}/decisions", json=body)


def last_message(client: TestClient, cid: str) -> str:
    return str(client.get(f"/claims/{cid}/messages").json()[-1]["text"])


def view(client: TestClient, cid: str) -> dict[str, Any]:
    out: dict[str, Any] = client.get(f"/claims/{cid}/customer").json()
    return out


def test_there_are_two_mock_handlers(client: TestClient) -> None:
    assert client.get("/handlers").json() == [SOFIA, DAVID]


def test_approve_is_a_decision_with_a_name_one_event_and_a_text_for_the_customer(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    res = decide(client, cid, "approve", reason="The invoice fits the damage.")
    assert res.status_code == 200
    c = claim(client, cid)
    # approved, then paid at once (issue #94): two status moves
    assert c["status"] == "paid" and c["outcome"] == "handler_review"
    [d] = c["decisions"]
    assert (d["action"], d["by"]) == ("approve", SOFIA)
    assert d["reason"] == "The invoice fits the damage." and d["time"]
    store = client.app.state.store  # type: ignore[attr-defined]
    events = [e for e in store.events_since(cid, 0) if e.actor == "handler"]
    assert len(events) == 1 and events[0].stage == "status"
    assert events[0].inputs == {
        "from": "in_review",
        "to": "approved",
        "by": SOFIA,
        "action": "approve",
    }
    assert events[0].outputs["why"] == "The invoice fits the damage."
    assert d["amount"] == 3000.0  # the proposal: 3,200 less the deductible of 200
    assert last_message(client, cid) == PAID
    v = view(client, cid)
    assert (v["status"], v["message"], v["step"]) == ("paid", PAID, "closed")
    assert v["status_line"] == "Paid: CHF 3,000.00 to your account ending 1002."
    # the letter: the amount and the two lines of the proposal (issue #88)
    assert v["letter"]["amount"] == 3000.0 and v["letter"]["note"] == ""
    assert [(x["label"], x["amount"]) for x in v["letter"]["lines"]] == [
        ("Documented loss", 3200.0),
        ("Deductible", -200.0),
    ]


def test_refuse_needs_a_reason_and_the_customer_never_reads_it(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    assert decide(client, cid, "refuse").status_code == 422
    assert decide(client, cid, "refuse", reason="   ").status_code == 422
    assert claim(client, cid)["status"] == "in_review"
    reason = "Exclusion 4.2: slow leak, not a sudden event. Looks like fraud."
    assert decide(client, cid, "refuse", reason=reason).status_code == 200
    c = claim(client, cid)
    assert c["status"] == "refused" and c["decisions"][0]["reason"] == reason
    # the customer: a fixed text, and nothing of the reason
    v = view(client, cid)
    assert v["status"] == "decided" and v["message"] == DECIDED
    assert last_message(client, cid) == DECIDED
    seen = str(v) + str(client.get(f"/claims/{cid}/messages").json())
    assert "Exclusion" not in seen and "fraud" not in seen.lower()


def test_a_decision_is_final_and_an_unknown_handler_or_action_is_refused(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    body = {"action": "approve", "by": "Somebody Else"}
    assert client.post(f"/claims/{cid}/decisions", json=body).status_code == 422
    assert decide(client, cid, "pay").status_code == 422
    assert decide(client, cid, "clear", reason="x").status_code == 409  # not in SIU
    assert decide(client, cid, "approve").status_code == 200
    assert decide(client, cid, "refuse", reason="changed my mind").status_code == 409
    assert claim(client, cid)["status"] == "paid"
    known = {"action": "approve", "by": SOFIA}
    assert client.post("/claims/CL-none/decisions", json=known).status_code == 404


def test_a_claim_still_in_the_chat_cannot_be_decided(client: TestClient) -> None:
    cid = client.post("/claims", json={"customer_id": "C-1002"}).json()["claim_id"]
    client.put(f"/claims/{cid}/policy", json={"policy_number": "HH-2024-0933"})
    assert decide(client, cid, "approve").status_code == 409
    assert claim(client, cid)["status"] == "open"


def test_investigate_and_clear_change_nothing_that_the_customer_sees(
    client: TestClient,
) -> None:
    """Non-negotiable 2: the customer cannot tell review from investigation."""
    cid = review_claim(client)
    before = (view(client, cid), client.get(f"/claims/{cid}/messages").json())
    assert decide(client, cid, "investigate").status_code == 422  # a reason
    assert (
        decide(client, cid, "investigate", reason="Same IBAN as CL-1.").status_code
        == 200
    )
    assert claim(client, cid)["status"] == "in_investigation"
    assert (view(client, cid), client.get(f"/claims/{cid}/messages").json()) == before
    # the investigator does his one task first (issue #92), then concludes
    task = {"by": SOFIA, "done": True, "note": "Called the provider."}
    assert (
        client.post(f"/claims/{cid}/investigation/tasks/T1", json=task).status_code
        == 200
    )
    assert decide(client, cid, "clear", reason="Provider confirmed.").status_code == 200
    assert claim(client, cid)["status"] == "in_review"
    assert (view(client, cid), client.get(f"/claims/{cid}/messages").json()) == before
    assert before[0]["message"] == TO_A_PERSON and before[0]["status"] == "in_review"
    assert [d["action"] for d in claim(client, cid)["decisions"]] == [
        "investigate",
        "clear",
    ]


def test_a_request_goes_to_the_customer_who_answers_with_a_document(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    ask = "Please send the original invoice of the plumber."
    assert decide(client, cid, "request").status_code == 422  # the text is required
    assert decide(client, cid, "request", reason=ask).status_code == 200
    assert claim(client, cid)["status"] == "waiting_customer"
    assert last_message(client, cid) == ask
    assert view(client, cid)["status"] == "waiting_customer"
    # the customer answers: the claim opens again, then the routing runs again
    assert upload(client, cid, MOCK / "claim-2-property" / "repair-invoice.pdf") == 201
    assert claim(client, cid)["status"] == "open"
    client.post(f"/claims/{cid}/messages", json={"text": "done"})
    c = claim(client, cid)
    assert c["status"] == "in_review" and len(c["decisions"]) == 1  # the history stays


@pytest.mark.parametrize(
    "text",
    [
        "We think this is fraud. Send the original.",
        "Your score is high: send the invoice.",
        "We opened an investigation. Send the invoice.",
    ],
)
def test_a_request_with_a_word_the_customer_must_not_read_is_refused(
    client: TestClient, text: str
) -> None:
    cid = review_claim(client)
    res = decide(client, cid, "request", reason=text)
    assert res.status_code == 422 and "forbidden word" in res.json()["detail"]
    assert claim(client, cid)["status"] == "in_review"
    assert text not in str(client.get(f"/claims/{cid}/messages").json())


def test_a_handler_can_decide_on_a_claim_that_waits(client: TestClient) -> None:
    cid = review_claim(client)
    decide(client, cid, "request", reason="Please send the original invoice.")
    assert decide(client, cid, "approve", by=SOFIA).status_code == 200
    c = claim(client, cid)
    assert c["status"] == "paid" and c["decisions"][-1]["by"] == SOFIA
    # decided: the claim does not open again
    assert upload(client, cid, MOCK / "claim-2-property" / "repair-invoice.pdf") == 409


def test_the_list_gives_the_opening_time_and_the_reasons(client: TestClient) -> None:
    cid = review_claim(client)
    [row] = client.get("/claims").json()
    assert row["claim_id"] == cid and row["status"] == "in_review"
    assert row["opened_at"].startswith("20") and row["opened_at"].endswith("Z")
    assert row["reasons"] == [
        "Claimed amount CHF 3,200.00 is above the straight-through limit of CHF 2,000."
    ]


def test_another_amount_than_the_proposal_needs_a_written_reason(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    assert claim(client, cid)["proposal"]["payable"] == 3000.0
    res = client.post(
        f"/claims/{cid}/decisions",
        json={"action": "approve", "by": SOFIA, "amount": 2500},
    )
    assert res.status_code == 422 and "written reason" in res.json()["detail"]
    body = {
        "action": "approve",
        "by": SOFIA,
        "amount": 2500,
        "reason": "The cabinets were 12 years old: CHF 500 less.",
    }
    assert client.post(f"/claims/{cid}/decisions", json=body).status_code == 200
    assert claim(client, cid)["decisions"][-1]["amount"] == 2500.0
    assert last_message(client, cid) == APPROVED.format(amount=2500)
    assert "cabinets" not in str(client.get(f"/claims/{cid}/messages").json())


def test_the_proposed_amount_given_again_needs_no_reason_and_a_refusal_has_no_amount(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    body = {"action": "approve", "by": SOFIA, "amount": 3000.0}
    assert client.post(f"/claims/{cid}/decisions", json=body).status_code == 200
    other = review_claim(client)
    body = {"action": "refuse", "by": DAVID, "amount": 99, "reason": "Exclusion 4.2."}
    assert client.post(f"/claims/{other}/decisions", json=body).status_code == 200
    assert claim(client, other)["decisions"][-1]["amount"] is None


def test_a_negative_amount_is_refused(client: TestClient) -> None:
    cid = review_claim(client)
    body = {"action": "approve", "by": SOFIA, "amount": -1, "reason": "x"}
    assert client.post(f"/claims/{cid}/decisions", json=body).status_code == 422

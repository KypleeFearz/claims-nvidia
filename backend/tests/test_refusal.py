"""A refusal needs two handlers and a reason for the customer (issue #111)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.handler import HANDLERS
from app.main import create_app
from app.views import DECIDED
from tests.test_handler import NOT_COVERED, review_claim

SOFIA, DAVID = HANDLERS
INTERNAL = "Slow leak over months: exclusion 4.2. The invoice looks made up."


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def decide(client: TestClient, cid: str, by: str, action: str, **more: Any) -> Any:
    return client.post(
        f"/claims/{cid}/decisions", json={"action": action, "by": by, **more}
    )


def customer_side(client: TestClient, cid: str) -> tuple[Any, Any]:
    return (
        client.get(f"/claims/{cid}/customer").json(),
        client.get(f"/claims/{cid}/messages").json(),
    )


def propose(client: TestClient, cid: str) -> Any:
    return decide(
        client, cid, DAVID, "refuse", reason=INTERNAL, customer_reason=NOT_COVERED
    )


def test_a_refusal_is_first_a_proposal_that_the_customer_does_not_see(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    before = customer_side(client, cid)
    res = propose(client, cid)
    assert res.status_code == 200
    c = res.json()
    assert c["status"] == "waiting_approval"
    [d] = c["decisions"]
    assert (d["action"], d["by"]) == ("propose_refusal", DAVID)
    assert d["reason"] == INTERNAL and d["customer_reason"] == NOT_COVERED
    assert customer_side(client, cid) == before
    assert c["reserve"] == 3000.0  # nothing is decided: the reserve stays


def test_the_same_handler_cannot_confirm_and_an_approval_is_not_a_confirmation(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    propose(client, cid)
    same = decide(client, cid, DAVID, "refuse")
    assert same.status_code == 422 and "second handler" in same.json()["detail"]
    other = decide(client, cid, SOFIA, "approve")
    assert other.status_code == 409 and "refuse it" in other.json()["detail"]
    assert client.get(f"/claims/{cid}").json()["status"] == "waiting_approval"


def test_the_second_handler_confirms_and_the_customer_reads_his_reason(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    propose(client, cid)
    res = decide(client, cid, SOFIA, "refuse")
    assert res.status_code == 200
    c = res.json()
    assert c["status"] == "refused" and c["reserve"] == 0.0 and c["payment"] is None
    last = c["decisions"][-1]
    assert (last["action"], last["by"]) == ("refuse", SOFIA)
    assert last["reason"] == INTERNAL and last["customer_reason"] == NOT_COVERED
    view, messages = customer_side(client, cid)
    assert view["status"] == "decided" and messages[-1]["text"] == DECIDED
    letter = view["letter"]
    assert letter["kind"] == "refusal" and letter["lines"] == []
    assert letter["note"].startswith(f"We cannot pay this claim. Reason: {NOT_COVERED}")
    assert "another claims specialist will look" in letter["note"]
    seen = str(view) + str(messages)
    assert "exclusion 4.2" not in seen.lower() and "made up" not in seen


def test_the_second_handler_can_return_a_refusal_to_review(client: TestClient) -> None:
    cid = review_claim(client)
    propose(client, cid)
    res = decide(client, cid, SOFIA, "return", reason="Ask the plumber first.")
    assert res.status_code == 200 and res.json()["status"] == "in_review"
    # back in review, a new decision is free: an approval now works
    assert decide(client, cid, SOFIA, "approve").json()["status"] == "paid"


@pytest.mark.parametrize(
    "said", ["We think this is fraud.", "Your score is too high.", ""]
)
def test_a_reason_for_the_customer_with_a_forbidden_word_or_empty_is_refused(
    client: TestClient, said: str
) -> None:
    cid = review_claim(client)
    res = decide(client, cid, DAVID, "refuse", reason=INTERNAL, customer_reason=said)
    assert res.status_code == 422
    assert client.get(f"/claims/{cid}").json()["status"] == "in_review"

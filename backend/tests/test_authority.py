"""Authority levels and the second approval (issue #105, package B3, slice 2)."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.handler import AUTHORITY, HANDLERS
from app.main import create_app
from app.views import APPROVED
from tests.test_handler import review_claim

SENIOR, JUNIOR = HANDLERS  # Sofia Brunner, David Frei


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def claim(client: TestClient, cid: str) -> dict[str, Any]:
    out: dict[str, Any] = client.get(f"/claims/{cid}").json()
    return out


def decide(client: TestClient, cid: str, by: str, action: str, **more: Any) -> Any:
    body = {"action": action, "by": by, **more}
    return client.post(f"/claims/{cid}/decisions", json=body)


def customer_side(client: TestClient, cid: str) -> tuple[Any, Any]:
    return (
        client.get(f"/claims/{cid}/customer").json(),
        client.get(f"/claims/{cid}/messages").json(),
    )


def test_each_handler_has_a_limit_and_the_page_can_read_it(client: TestClient) -> None:
    assert AUTHORITY == {SENIOR: 20000, JUNIOR: 2500}
    assert client.get("/handlers").json() == [SENIOR, JUNIOR]
    assert client.get("/handlers/authority").json() == {SENIOR: 20000, JUNIOR: 2500}


def test_an_approval_above_the_limit_is_a_proposal_that_pays_nothing(
    client: TestClient,
) -> None:
    cid = review_claim(client)  # the proposal: CHF 3,000
    before = customer_side(client, cid)
    res = decide(client, cid, JUNIOR, "approve")
    assert res.status_code == 200
    c = res.json()
    assert c["status"] == "waiting_approval" and c["payment"] is None
    [d] = c["decisions"]
    assert (d["action"], d["by"], d["amount"]) == ("propose", JUNIOR, 3000.0)
    assert c["reserve"] == 3000.0
    assert customer_side(client, cid) == before  # the customer sees a review, as before
    store = client.app.state.store  # type: ignore[attr-defined]
    move = [e for e in store.events_since(cid, 0) if e.actor == "handler"][-1]
    assert (
        move.inputs["to"] == "waiting_approval" and move.inputs["action"] == "propose"
    )
    assert "above the limit of David Frei (CHF 2,500)" in move.outputs["why"]
    [row] = client.get("/claims").json()
    assert row["status"] == "waiting_approval"  # in the queue of the handlers


def test_the_second_approval_is_another_handler_with_the_authority(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    decide(client, cid, JUNIOR, "approve")
    again = decide(client, cid, JUNIOR, "approve")
    assert again.status_code == 422 and "second handler" in again.json()["detail"]
    assert decide(client, cid, JUNIOR, "refuse", reason="x").status_code == 409
    res = decide(client, cid, SENIOR, "approve", amount=9999)  # the amount is fixed
    assert res.status_code == 200
    c = res.json()
    assert c["status"] == "paid" and c["payment"]["amount"] == 3000.0
    assert [(d["action"], d["by"]) for d in c["decisions"]] == [
        ("propose", JUNIOR),
        ("approve", SENIOR),
    ]
    last = client.get(f"/claims/{cid}/messages").json()[-1]["text"]
    assert last == APPROVED.format(amount=3000)


def test_the_second_handler_can_return_the_claim_to_review_with_a_reason(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    decide(client, cid, JUNIOR, "approve")
    before = customer_side(client, cid)
    assert decide(client, cid, SENIOR, "return").status_code == 422  # a reason
    res = decide(client, cid, SENIOR, "return", reason="Ask for a second quote first.")
    assert res.status_code == 200 and res.json()["status"] == "in_review"
    assert customer_side(client, cid) == before
    # back in review: `return` is only for a claim that waits for an approval
    assert decide(client, cid, SENIOR, "return", reason="x").status_code == 409


def test_an_approval_under_the_limit_needs_nobody_else(client: TestClient) -> None:
    cid = review_claim(client)
    body = {"amount": 2500, "reason": "Old cabinets: CHF 500 less."}
    res = decide(client, cid, JUNIOR, "approve", **body)
    assert res.status_code == 200 and res.json()["status"] == "paid"
    other = review_claim(client)
    assert decide(client, other, SENIOR, "approve").json()["status"] == "paid"


def test_an_amount_above_every_limit_stays_a_proposal(client: TestClient) -> None:
    cid = review_claim(client)
    body = {"amount": 25000, "reason": "Rebuild of the whole kitchen."}
    res = decide(client, cid, SENIOR, "approve", **body)
    assert res.json()["status"] == "waiting_approval"
    res = decide(client, cid, JUNIOR, "approve")
    assert res.status_code == 422 and "above your limit" in res.json()["detail"]

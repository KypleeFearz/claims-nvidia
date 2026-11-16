"""The life of a claim (issue #72): statuses, allowed moves, a claim that waits for a
document, and the list of claims. `local` mode, through the HTTP API."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.lifecycle import MOVES, IllegalMove
from app.main import create_app
from app.models import ClaimStatus, Outcome
from app.views import TO_A_PERSON, VALIDATED
from tests.builders import signal
from tests.reader_contract import MOCK

STORY = (
    "A football broke the living-room window on 14 September 2026. I claim CHF 1,260."
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.delenv("CLAIMS_MODE", raising=False)  # `local`
    with TestClient(create_app()) as c:
        yield c


def upload(client: TestClient, cid: str, path: Path) -> int:
    res = client.post(
        f"/claims/{cid}/documents", files={"file": (path.name, path.read_bytes())}
    )
    return res.status_code


def window_claim(client: TestClient) -> str:
    """Kit story `kit-lukas-household`: a repair quote and no damage photo."""
    cid = str(client.post("/claims", json={"customer_id": "C-1003"}).json()["claim_id"])
    client.put(f"/claims/{cid}/policy", json={"policy_number": "HH-2026-0102"})
    client.post(f"/claims/{cid}/messages", json={"text": STORY})
    assert upload(client, cid, MOCK / "kit-lukas-household" / "repair-quote.pdf") == 201
    return cid


def say(client: TestClient, cid: str, text: str) -> str:
    res = client.post(f"/claims/{cid}/messages", json={"text": text})
    return str(res.json()[-1]["text"])


def test_every_status_has_its_moves_and_only_a_wait_opens_again() -> None:
    assert set(MOVES) == set(ClaimStatus)
    assert MOVES[ClaimStatus.waiting_customer] == {ClaimStatus.open}
    for end in ("validated", "in_review", "in_investigation"):
        assert MOVES[ClaimStatus(end)] == set()


def test_a_claim_with_a_missing_document_waits_and_tells_the_customer_what_is_missing(
    client: TestClient,
) -> None:
    cid = window_claim(client)
    reply = say(client, cid, "done")
    claim = client.get(f"/claims/{cid}").json()
    assert claim["status"] == "waiting_customer"
    assert claim["outcome"] == "handler_review"  # the pipeline panel still shows it
    assert "damage photo" in reply and reply.endswith(TO_A_PERSON)
    view = client.get(f"/claims/{cid}/customer").json()
    assert view["status"] == "waiting_customer" and reply.endswith(view["message"])


def test_the_customer_adds_the_document_later_and_the_claim_gets_a_new_outcome(
    client: TestClient,
) -> None:
    cid = window_claim(client)
    say(client, cid, "done")
    photo = MOCK / "claim-2-property" / "damage-photo-1.jpg"
    assert upload(client, cid, photo) == 201  # the second visit
    claim = client.get(f"/claims/{cid}").json()
    assert claim["status"] == "open" and claim["outcome"] is None
    assert claim["agent_state"] == "ask_documents"
    assert say(client, cid, "done").endswith(VALIDATED)
    claim = client.get(f"/claims/{cid}").json()
    assert claim["status"] == "validated" and claim["outcome"] == "straight_through"
    assert [s["code"] for s in claim["signals"]] == []


def test_each_status_move_is_one_event_with_who_did_it(client: TestClient) -> None:
    cid = window_claim(client)
    say(client, cid, "done")
    upload(client, cid, MOCK / "claim-2-property" / "damage-photo-1.jpg")
    say(client, cid, "done")
    store = client.app.state.store  # type: ignore[attr-defined]
    moves = [e for e in store.events_since(cid, 0) if e.stage == "status"]
    assert [(e.inputs["from"], e.inputs["to"], e.inputs["by"]) for e in moves] == [
        ("open", "waiting_customer", "system"),
        ("waiting_customer", "open", "customer"),
        ("open", "validated", "system"),
    ]
    # the event of the second visit keeps what the first routing said
    assert moves[1].outputs["outcome_before"] == "handler_review"


def test_a_claim_that_does_not_wait_stays_closed_for_uploads(
    client: TestClient,
) -> None:
    cid = window_claim(client)
    photo = MOCK / "claim-2-property" / "damage-photo-1.jpg"
    upload(client, cid, photo)
    say(client, cid, "done")
    assert client.get(f"/claims/{cid}").json()["status"] == "validated"
    assert upload(client, cid, photo) == 409


def test_a_move_that_is_not_allowed_is_refused_and_update_claim_cannot_set_a_status(
    client: TestClient,
) -> None:
    store = client.app.state.store  # type: ignore[attr-defined]
    cid = client.post("/claims", json={"customer_id": "C-1003"}).json()["claim_id"]
    store.save_routing(cid, 0, Outcome.straight_through, [])
    with pytest.raises(IllegalMove):
        store.set_status(cid, ClaimStatus.open, "system")
    with pytest.raises(ValueError, match="set_status"):
        store.update_claim(cid, {"status": "open"})
    assert store.get_claim(cid).status == ClaimStatus.validated


def test_the_wait_says_nothing_about_the_outcome(client: TestClient) -> None:
    """Non-negotiable 2: a missing document gives the same status and the same text
    for `handler_review` and for `investigation`."""
    store = client.app.state.store  # type: ignore[attr-defined]
    views = []
    for outcome, extra in (
        (Outcome.handler_review, []),
        (Outcome.investigation, [signal("R002", 40)]),
    ):
        cid = window_claim(client)
        say(client, cid, "done")
        claim = store.get_claim(cid)
        store.reopen(cid, "customer")
        store.update_claim(
            cid, {"signals": [s.model_dump() for s in [*claim.signals, *extra]]}
        )
        client.app.state.gateway.call(cid, "assess_and_route", {"claim_id": cid})  # type: ignore[attr-defined]
        assert store.get_claim(cid).outcome == outcome
        view = client.get(f"/claims/{cid}/customer").json()
        views.append((view["status"], view["message"]))
    assert views[0] == views[1] and views[0][0] == "waiting_customer"


def test_the_list_of_claims(client: TestClient) -> None:
    assert client.get("/claims").json() == []
    cid = window_claim(client)
    say(client, cid, "done")
    other = client.post("/claims", json={"customer_id": "C-1001"}).json()["claim_id"]
    rows = client.get("/claims").json()
    assert [r["claim_id"] for r in rows] == [cid, other]
    assert rows[0] == {
        "claim_id": cid,
        "customer": "Lukas Meier",
        "policy_number": "HH-2026-0102",
        "type": "property",
        "claimed_amount": 1260.0,
        "status": "waiting_customer",
        "outcome": "handler_review",
        "score": 25,
    }
    assert rows[1]["status"] == "open" and rows[1]["outcome"] is None


def test_required_documents_says_in_code_what_is_present_and_what_is_missing(
    client: TestClient,
) -> None:
    """Issue #78: the model does not work out what is missing, the tool says it."""
    gateway = client.app.state.gateway  # type: ignore[attr-defined]
    cid = window_claim(client)  # the repair quote, no damage photo
    out = gateway.call(cid, "required_documents", {"claim_id": cid})
    assert [(d["label"], d["present"]) for d in out["documents"]] == [
        ("a repair invoice or a repair quote", True),
        ("a damage photo", False),
    ]
    assert out["missing"] == ["a damage photo"]
    upload(client, cid, MOCK / "claim-2-property" / "damage-photo-1.jpg")
    out = gateway.call(cid, "required_documents", {"claim_id": cid})
    assert out["missing"] == [] and all(d["present"] for d in out["documents"])

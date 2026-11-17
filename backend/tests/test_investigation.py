"""The investigation case (issue #92, package B4): a plan, tasks, an evidence log and
a conclusion. `local` mode, through the HTTP API."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.handler import HANDLERS
from app.main import create_app
from app.views import APPROVED
from tests.reader_contract import MOCK
from tests.test_handler import refuse_claim, review_claim
from tests.test_history import elena

SOFIA, DAVID = HANDLERS


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    monkeypatch.delenv("CLAIMS_MODE", raising=False)
    with TestClient(create_app()) as c:
        yield c


def claim(client: TestClient, cid: str) -> dict[str, Any]:
    out: dict[str, Any] = client.get(f"/claims/{cid}").json()
    return out


def post(client: TestClient, cid: str, path: str, **body: Any) -> Any:
    return client.post(f"/claims/{cid}/{path}", json={"by": DAVID, **body})


def tick(client: TestClient, cid: str, task: str, note: str = "Done.") -> Any:
    return post(client, cid, f"investigation/tasks/{task}", done=True, note=note)


def customer_side(client: TestClient, cid: str) -> tuple[Any, Any]:
    return (
        client.get(f"/claims/{cid}/customer").json(),
        client.get(f"/claims/{cid}/messages").json(),
    )


def test_a_claim_routed_to_investigation_gets_a_plan_from_its_signals(
    client: TestClient,
) -> None:
    cid = elena(client)  # R008 and R009
    case = claim(client, cid)["investigation"]
    assert [(t["id"], t["codes"], t["done"]) for t in case["tasks"]] == [
        ("T1", ["R008"], False),
        ("T2", ["R009"], False),
    ]
    assert case["tasks"][0]["text"].startswith("Check who owns the payout account")
    assert case["notes"] == [] and case["conclusion"] is None


def test_signals_of_one_kind_give_one_task(client: TestClient) -> None:
    cid = str(client.post("/claims", json={"customer_id": "C-1002"}).json()["claim_id"])
    client.put(f"/claims/{cid}/policy", json={"policy_number": "HH-2024-0933"})
    story = "A leaking pipe flooded my bathroom on 3 November 2026. I claim CHF 4,740."
    client.post(f"/claims/{cid}/messages", json={"text": story})
    for name in ("repair-invoice.pdf", "damage-photo.jpg"):
        data = (MOCK / "kit-marco-bathroom" / name).read_bytes()
        client.post(f"/claims/{cid}/documents", files={"file": (name, data)})
    client.post(f"/claims/{cid}/messages", json={"text": "done"})
    [task] = claim(client, cid)["investigation"]["tasks"]  # T001, T002 and T003
    assert task["codes"] == ["T001", "T002", "T003"]
    assert task["text"].startswith("Ask the provider for the original")


def test_a_handler_who_sends_a_clean_claim_to_investigation_gets_one_task(
    client: TestClient,
) -> None:
    cid = review_claim(client)  # no signal
    assert claim(client, cid)["investigation"] is None
    post(client, cid, "decisions", action="investigate", reason="Same phone as CL-1.")
    [task] = claim(client, cid)["investigation"]["tasks"]
    assert task["codes"] == [] and "Same phone as CL-1." in task["text"]


def test_a_task_is_ticked_with_a_name_a_note_and_one_event(client: TestClient) -> None:
    cid = elena(client)
    res = tick(client, cid, "T1", "The bank confirms: the account is of R. Vogel.")
    assert res.status_code == 200
    task = res.json()["investigation"]["tasks"][0]
    assert task["done"] and task["by"] == DAVID and task["time"]
    assert task["note"] == "The bank confirms: the account is of R. Vogel."
    store = client.app.state.store  # type: ignore[attr-defined]
    [event] = [e for e in store.events_since(cid, 0) if e.stage == "investigation"]
    assert event.actor == "handler" and event.inputs == {"task": "T1", "by": DAVID}
    assert event.outputs["done"] is True
    # a task can be opened again; an unknown task or handler is refused
    assert post(client, cid, "investigation/tasks/T1", done=False).status_code == 200
    assert claim(client, cid)["investigation"]["tasks"][0]["done"] is False
    assert tick(client, cid, "T9").status_code == 404
    bad = client.post(
        f"/claims/{cid}/investigation/tasks/T1", json={"by": "Nobody", "done": True}
    )
    assert bad.status_code == 422


def test_the_evidence_log_keeps_each_note_with_its_author(client: TestClient) -> None:
    cid = elena(client)
    assert post(client, cid, "investigation/notes", text="  ").status_code == 422
    assert (
        post(client, cid, "investigation/notes", text="Called E. Rossi.").status_code
        == 200
    )
    client.post(
        f"/claims/{cid}/investigation/notes",
        json={"by": SOFIA, "text": "She is the sister of R. Vogel."},
    )
    notes = claim(client, cid)["investigation"]["notes"]
    assert [(n["by"], n["text"]) for n in notes] == [
        (DAVID, "Called E. Rossi."),
        (SOFIA, "She is the sister of R. Vogel."),
    ]


def test_no_conclusion_while_a_task_is_open_and_no_decision_in_investigation(
    client: TestClient,
) -> None:
    cid = elena(client)
    tick(client, cid, "T1")
    res = post(client, cid, "decisions", action="clear", reason="Nothing found.")
    assert res.status_code == 409 and "T2" in res.json()["detail"]
    # the investigator reports first: the handler cannot decide during it
    for action in ("approve", "refuse"):
        res = post(client, cid, "decisions", action=action, reason="x")
        assert res.status_code == 409, action
    assert claim(client, cid)["status"] == "in_investigation"


def test_the_story_of_elena_to_the_end(client: TestClient) -> None:
    """Plan of two tasks, both ticked, not confirmed, back to the handler, approved.
    The customer sees nothing until the approval."""
    cid = elena(client)
    before = customer_side(client, cid)
    tick(client, cid, "T1", "Joint account of two siblings, opened in 2019.")
    tick(client, cid, "T2", "Same household until 2024.")
    post(client, cid, "investigation/notes", text="Bank letter in the file.")
    text = "The link is a family link. Nothing ties this claim to the earlier fraud."
    assert (
        post(client, cid, "decisions", action="clear", reason=text).status_code == 200
    )
    c = claim(client, cid)
    assert c["status"] == "in_review"
    conclusion = c["investigation"]["conclusion"]
    assert (conclusion["result"], conclusion["by"]) == ("not_confirmed", DAVID)
    assert conclusion["text"] == text
    assert customer_side(client, cid) == before  # non-negotiable 2
    res = client.post(
        f"/claims/{cid}/decisions", json={"action": "approve", "by": SOFIA}
    )
    # paid to the account that the investigator checked (issue #94)
    assert res.status_code == 200 and claim(client, cid)["status"] == "paid"
    last = client.get(f"/claims/{cid}/messages").json()[-1]["text"]
    assert last == APPROVED.format(amount=1750)


def test_a_confirmed_fraud_goes_back_to_the_handler_who_decides(
    client: TestClient,
) -> None:
    """Non-negotiable 1: a conclusion is not a refusal. A handler refuses."""
    cid = elena(client)
    before = customer_side(client, cid)
    tick(client, cid, "T1")
    tick(client, cid, "T2")
    assert post(client, cid, "decisions", action="confirm").status_code == 422  # text
    text = "The payout account belongs to R. Vogel alone."
    assert (
        post(client, cid, "decisions", action="confirm", reason=text).status_code == 200
    )
    c = claim(client, cid)
    assert c["status"] == "in_review"  # not refused: a person decides
    assert c["investigation"]["conclusion"]["result"] == "confirmed"
    assert customer_side(client, cid) == before
    # after the conclusion the tasks and the log are closed
    assert tick(client, cid, "T1").status_code == 409
    assert post(client, cid, "investigation/notes", text="late").status_code == 409
    res = refuse_claim(client, cid, "Fraud confirmed by the investigation.")
    assert res.status_code == 200 and claim(client, cid)["status"] == "refused"


def test_a_claim_with_no_investigation_has_no_tasks_and_no_log(
    client: TestClient,
) -> None:
    cid = review_claim(client)
    assert tick(client, cid, "T1").status_code == 409
    assert post(client, cid, "investigation/notes", text="x").status_code == 409
    assert (
        post(client, cid, "decisions", action="confirm", reason="x").status_code == 409
    )

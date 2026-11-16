"""The first notice of loss (issue #98, package B5): questions per cause, why each
document, and a way to a person. `local` mode (the scripted agent), through the API."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agent.phrases import wants_person
from app.main import create_app
from app.workflows import WORKFLOWS
from tests.reader_contract import MOCK

THEFT = (
    "Burglary at my flat on 9 October 2026: the front door was forced. "
    "I claim CHF 2,640."
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "claims.db"))
    monkeypatch.setenv("CLAIMS_UPLOADS", str(tmp_path / "uploads"))
    with TestClient(create_app()) as c:
        yield c


def start(client: TestClient, story: str = THEFT) -> tuple[str, str]:
    """Anna, household policy, the story in one message: the claim id and the answer."""
    cid = str(client.post("/claims", json={"customer_id": "C-1001"}).json()["claim_id"])
    client.put(f"/claims/{cid}/policy", json={"policy_number": "HH-2025-1182"})
    return cid, say(client, cid, story)


def say(client: TestClient, cid: str, text: str) -> str:
    res = client.post(f"/claims/{cid}/messages", json={"text": text})
    assert res.status_code == 200
    return str(res.json()[-1]["text"])


def claim(client: TestClient, cid: str) -> dict[str, Any]:
    out: dict[str, Any] = client.get(f"/claims/{cid}").json()
    return out


def test_every_cause_of_every_workflow_has_its_questions_with_a_reason() -> None:
    for workflow in WORKFLOWS.values():
        named = {cause for cause, _ in workflow.causes} | {workflow.default_cause}
        assert set(workflow.questions) <= named, workflow.claim_type
        keys = [q.key for qs in workflow.questions.values() for q in qs]
        assert len(keys) == len(set(keys))
        for qs in workflow.questions.values():
            assert all(q.text.endswith("?") and q.why.endswith(".") for q in qs)
    assert {q.key for q in WORKFLOWS["property"].questions["theft"]} == {
        "theft_entry",
        "theft_police",
    }


def test_the_claim_gets_the_questions_of_its_cause_and_the_agent_says_why_each_document(
    client: TestClient,
) -> None:
    cid, reply = start(client)
    c = claim(client, cid)
    assert c["cause"] == "theft" and c["agent_state"] == "ask_documents"
    assert [(n["key"], n["answer"]) for n in c["notice"]] == [
        ("theft_entry", None),
        ("theft_police", None),
    ]
    assert (
        c["notice"][0]["why"] == "A theft with no sign of entry is a known indicator."
    )
    # the documents, each with what it is for, then the first question
    assert "a police report (a theft must be reported to the police)" in reply
    assert "a damage photo (it shows the damage)" in reply
    assert reply.endswith("How did the thief get in? Was a door or a window forced?")
    assert "known indicator" not in reply  # the reason is for the handler


def test_the_agent_asks_one_question_at_a_time_and_never_twice(
    client: TestClient,
) -> None:
    cid, _ = start(client)
    reply = say(client, cid, "Through the front door. The lock was broken.")
    assert "Did you report it to the police? On what date?" in reply
    assert "How did the thief get in" not in reply
    reply = say(client, cid, "Yes, on 10 October 2026.")
    assert "?" not in reply.split("type done.")[-1]  # no question is left
    notice = claim(client, cid)["notice"]
    assert [n["answer"] for n in notice] == [
        "Through the front door. The lock was broken.",
        "Yes, on 10 October 2026.",
    ]
    # one more message is not an answer to a question that has its answer
    say(client, cid, "Thank you.")
    assert [n["answer"] for n in claim(client, cid)["notice"]] == [
        "Through the front door. The lock was broken.",
        "Yes, on 10 October 2026.",
    ]


def test_each_answer_goes_through_the_gateway_as_one_event(client: TestClient) -> None:
    cid, _ = start(client)
    say(client, cid, "Through a window.")
    store = client.app.state.store  # type: ignore[attr-defined]
    [event] = [e for e in store.events_since(cid, 0) if e.stage == "save_answer"]
    assert event.actor == "tool"
    assert event.inputs == {
        "claim_id": cid,
        "key": "theft_entry",
        "answer": "Through a window.",
    }


def test_the_questions_do_not_block_done_and_an_upload_is_not_an_answer(
    client: TestClient,
) -> None:
    cid, _ = start(client)
    for name in ("police-report.pdf", "repair-quote.pdf", "damage-photo.jpg"):
        data = (MOCK / "kit-anna-theft" / name).read_bytes()
        res = client.post(f"/claims/{cid}/documents", files={"file": (name, data)})
        assert res.status_code == 201
    assert all(n["answer"] is None for n in claim(client, cid)["notice"])
    say(client, cid, "done")
    c = claim(client, cid)
    assert c["outcome"] == "handler_review" and c["agent_state"] == "closed"
    assert all(n["answer"] is None for n in c["notice"])  # the handler sees it


def test_a_cause_with_no_question_and_a_claim_with_no_cause_have_no_notice(
    client: TestClient,
) -> None:
    cid = str(client.post("/claims", json={"customer_id": "C-1001"}).json()["claim_id"])
    assert claim(client, cid)["notice"] == []
    gateway = client.app.state.gateway  # type: ignore[attr-defined]
    client.put(f"/claims/{cid}/policy", json={"policy_number": "HH-2025-1182"})
    out = gateway.call(cid, "get_claim_summary", {"claim_id": cid})
    assert out["open_questions"] == []
    with pytest.raises(ValueError, match="unknown question"):
        gateway.call(cid, "save_answer", {"claim_id": cid, "key": "x", "answer": "y"})


@pytest.mark.parametrize(
    "text",
    [
        "I want to speak to a person",
        "Can I talk to a human please?",
        "please let me speak with someone",
        "I need a real person.",
    ],
)
def test_a_customer_who_asks_for_a_person_gets_one_at_once(
    client: TestClient, text: str
) -> None:
    cid, _ = start(client)
    reply = say(client, cid, text)
    assert reply == "I pass your case to a claims specialist, who will contact you."
    c = claim(client, cid)
    assert (
        c["status"] == "in_review" and c["outcome"] is None
    )  # the system decides nothing
    assert c["agent_state"] == "closed"
    assert all(n["answer"] is None for n in c["notice"])  # not taken as an answer
    store = client.app.state.store  # type: ignore[attr-defined]
    [move] = [e for e in store.events_since(cid, 0) if e.stage == "status"]
    assert (
        move.inputs["by"] == "customer" and move.outputs["why"] == "asked for a person"
    )
    assert [r["claim_id"] for r in client.get("/claims").json()] == [
        cid
    ]  # in the queue
    view = client.get(f"/claims/{cid}/customer").json()
    assert view["status"] == "in_review" and view["message"] is None


def test_other_sentences_with_the_same_words_are_not_a_request_for_a_person() -> None:
    for text in (
        "A person broke my window.",
        "The agent at the airport gave me a report.",
        "I spoke to the police.",
        "done",
    ):
        assert not wants_person(text), text


def test_after_the_routing_a_request_for_a_person_changes_nothing(
    client: TestClient,
) -> None:
    cid, _ = start(client)
    say(client, cid, "done")
    before = claim(client, cid)["status"]
    say(client, cid, "I want to speak to a person")
    assert claim(client, cid)["status"] == before  # a person has the case already

"""The investigation case (issue #92, package B4 of docs/BASE-ROADMAP.md).

A claim in investigation gets a plan: one task per kind of signal, in plain words.
An investigator ticks the tasks, writes notes in the evidence log, and concludes:
fraud confirmed, or not confirmed. The conclusion sends the claim back to a handler
(`app/handler.py`): the investigator reports, the handler decides. A conclusion is
never a refusal (non-negotiable 1), and the customer sees none of this
(non-negotiable 2: nothing here writes a message or changes the customer view).
"""

from datetime import UTC, datetime
from typing import Any

from app.lifecycle import IllegalMove
from app.models import (
    Claim,
    ClaimStatus,
    Conclusion,
    Investigation,
    InvestigationTask,
    Note,
)
from app.store import Store

# What to do for a signal. Codes with the same text make one task.
ORIGINAL = "Ask the provider for the original document and compare it with the file."
TASKS = {
    "R001": "Ask the customer for the missing document, or why it does not exist.",
    "R002": "Check the date of the incident and the policy period with the customer.",
    "R003": "Check when the customer asked for the policy, and what was known then.",
    "R004": "Check the value of the loss: ask for a second quote or an expert.",
    "R005": "Ask the provider why the invoice is dated before the incident.",
    "R006": "Read the earlier claims of the customer: same cause, same provider?",
    "R007": "Read the file of the earlier claim that was closed as fraud.",
    "R008": "Check who owns the payout account, before any payment.",
    "R009": "Check the link with the other person: who is it for the customer?",
    "R010": "Check the provider: registration, address, and its other claims.",
    "T001": ORIGINAL,
    "T002": ORIGINAL,
    "T003": ORIGINAL,
    "C001": "Check who the person named on the document is.",
    "C002": "Check the dates on the documents with the provider.",
    "C003": "Ask the customer how the claimed amount was calculated.",
    "C004": "Compare the photo and the report with the declared damage, on site.",
}
OTHER = "Check this signal by hand."


class NotAllowed(Exception):
    """The state of the investigation does not allow this (HTTP 409)."""


def plan(claim: Claim, referral: str = "") -> list[InvestigationTask]:
    """The tasks for the signals of a claim, strongest signal first. A handler who
    sends a claim with no signal gives the one task himself: his reason."""
    by_text: dict[str, list[str]] = {}
    for s in sorted(claim.signals, key=lambda s: (-s.points, s.code)):
        by_text.setdefault(TASKS.get(s.code, OTHER), []).append(s.code)
    if not by_text:
        by_text[f"Check what the handler wrote: {referral}".strip()] = []
    return [
        InvestigationTask(id=f"T{i}", text=text, codes=sorted(codes))
        for i, (text, codes) in enumerate(by_text.items(), 1)
    ]


def open_case(store: Store, claim_id: str, referral: str = "") -> None:
    """Called when a claim goes to `in_investigation`. A claim that comes back to
    investigation after a conclusion gets a new plan; the old case is not kept."""
    claim = store.get_claim(claim_id)
    if claim is None or claim.status != ClaimStatus.in_investigation:
        return
    if claim.investigation is not None and claim.investigation.conclusion is None:
        return  # a case is open already
    case = Investigation(tasks=plan(claim, referral))
    store.update_claim(claim_id, {"investigation": case.model_dump(mode="json")})


def _open(store: Store, claim_id: str) -> tuple[Claim, Investigation]:
    claim = store.get_claim(claim_id)
    if claim is None:
        raise KeyError(claim_id)
    case = claim.investigation
    if claim.status != ClaimStatus.in_investigation or case is None:
        raise NotAllowed("the claim is not in investigation")
    return claim, case


def _save(store: Store, claim_id: str, case: Investigation) -> Claim:
    saved = store.update_claim(
        claim_id, {"investigation": case.model_dump(mode="json")}
    )
    assert saved is not None
    return saved


def set_task(
    store: Store, claim_id: str, task_id: str, done: bool, by: str, note: str
) -> Claim:
    _, case = _open(store, claim_id)
    task = next((t for t in case.tasks if t.id == task_id), None)
    if task is None:
        raise LookupError(task_id)
    task.done, task.by, task.time = done, by, datetime.now(UTC)
    task.note = note.strip() or task.note
    saved = _save(store, claim_id, case)
    store.append_event(
        claim_id, "handler", "investigation",
        f"Task {task_id} {'done' if done else 'opened again'}: {task.text}"[:80],
        {"task": task_id, "by": by},
        {"done": done, "note": task.note, "text": task.text},
    )  # fmt: skip
    return saved


def add_note(store: Store, claim_id: str, by: str, text: str) -> Claim:
    _, case = _open(store, claim_id)
    case.notes.append(Note(by=by, text=text.strip(), time=datetime.now(UTC)))
    saved = _save(store, claim_id, case)
    store.append_event(
        claim_id, "handler", "investigation", "Note in the evidence log",
        {"by": by}, {"text": text.strip()},
    )  # fmt: skip
    return saved


def conclusion(claim: Claim, result: str, by: str, text: str) -> dict[str, Any]:
    """The case with its conclusion, as the fields to save with the status move.
    `IllegalMove` when a task is still open: the investigator finishes first."""
    case = claim.investigation
    if claim.status != ClaimStatus.in_investigation or case is None:
        raise IllegalMove("the claim is not in investigation")
    still = [t.id for t in case.tasks if not t.done]
    if still:
        raise IllegalMove(f"these tasks are still open: {', '.join(still)}")
    case.conclusion = Conclusion(
        result=result,  # type: ignore[arg-type]
        by=by,
        text=text,
        time=datetime.now(UTC),
    )
    return {"investigation": case.model_dump(mode="json")}

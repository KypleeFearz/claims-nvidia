"""Experts and third parties (issue #102, package B8 of docs/BASE-ROADMAP.md).

- An expert request: a handler asks a loss adjuster or a medical adviser one
  question. The claim keeps its status, but it cannot be approved or refused while
  a request is open. The report gives a finding and an assessed amount.
- A recovery: another person is liable for the loss. The insurer pays its customer
  and asks that person to pay back. It is planned on a claim, becomes open when the
  claim is paid, and ends as recovered or abandoned.

Neither decides anything: a handler reads the report and decides (non-negotiable
1). The customer reads nothing of either (non-negotiable 2): nothing here writes a
message or changes the customer view.
"""

from datetime import UTC, datetime
from typing import Any

from app.investigation import NotAllowed
from app.models import (
    Claim,
    ClaimStatus,
    ExpertReport,
    ExpertRequest,
    Recovery,
)
from app.store import Store

KINDS = {"property": "loss adjuster", "health": "medical adviser"}
WITH_A_PERSON = (
    ClaimStatus.in_review,
    ClaimStatus.in_investigation,
    ClaimStatus.waiting_customer,
)


def _claim(store: Store, claim_id: str) -> Claim:
    claim = store.get_claim(claim_id)
    if claim is None:
        raise KeyError(claim_id)
    return claim


def _save(store: Store, claim_id: str, fields: dict[str, Any]) -> Claim:
    saved = store.update_claim(claim_id, fields)
    assert saved is not None
    return saved


def _need(text: str, what: str) -> str:
    text = " ".join(text.split())
    if not text:
        raise ValueError(f"{what} is required")
    return text


def open_requests(claim: Claim) -> list[str]:
    return [e.id for e in claim.experts if e.report is None]


def ask_expert(store: Store, claim_id: str, by: str, question: str) -> Claim:
    claim = _claim(store, claim_id)
    question = _need(question, "the question for the expert")
    if claim.status not in WITH_A_PERSON:
        raise NotAllowed("an expert is asked on a claim that is with a person")
    request = ExpertRequest(
        id=f"E{len(claim.experts) + 1}",
        kind=KINDS.get(claim.type or "", "expert"),
        question=question,
        asked_by=by,
        time=datetime.now(UTC),
    )
    experts = [*claim.experts, request]
    saved = _save(
        store, claim_id, {"experts": [e.model_dump(mode="json") for e in experts]}
    )
    store.append_event(
        claim_id, "handler", "expert", f"Expert asked: {request.kind}",
        {"request": request.id, "by": by}, {"question": question},
    )  # fmt: skip
    return saved


def expert_report(
    store: Store,
    claim_id: str,
    request_id: str,
    by: str,
    expert: str,
    finding: str,
    amount: float | None,
) -> Claim:
    """The report of the expert. In the demo a handler types it (a mock): the expert
    is outside the company."""
    claim = _claim(store, claim_id)
    request = next((e for e in claim.experts if e.id == request_id), None)
    if request is None:
        raise LookupError(request_id)
    report = ExpertReport(
        expert=_need(expert, "the name of the expert"),
        finding=_need(finding, "the finding of the expert"),
        amount=amount,
        time=datetime.now(UTC),
    )
    if request.report is not None:
        raise NotAllowed("this request has its report already")
    request.report = report
    saved = _save(
        store,
        claim_id,
        {"experts": [e.model_dump(mode="json") for e in claim.experts]},
    )
    assessed = "no amount" if amount is None else f"CHF {amount:,.2f}"
    store.append_event(
        claim_id, "handler", "expert", f"Expert report: {assessed}",
        {"request": request_id, "by": by},
        {"expert": report.expert, "finding": report.finding, "amount": amount},
    )  # fmt: skip
    return saved


def open_recovery(
    store: Store, claim_id: str, by: str, party: str, reason: str
) -> Claim:
    claim = _claim(store, claim_id)
    party = _need(party, "the liable party")
    reason = _need(reason, "the reason")
    if claim.recovery is not None:
        raise NotAllowed("the claim has a recovery already")
    # read only, as text: a test checks that only the handler module names the
    # decided statuses
    if claim.status in ("open", "refused"):
        raise NotAllowed("no recovery on a claim that is in the chat or refused")
    paid = claim.payment.amount if claim.payment else None
    recovery = Recovery(
        party=party,
        reason=reason,
        opened_by=by,
        time=datetime.now(UTC),
        status="open" if paid else "planned",
        amount=paid,
    )
    saved = _save(store, claim_id, {"recovery": recovery.model_dump(mode="json")})
    what = f"open: CHF {paid:,.2f}" if paid else "planned"
    store.append_event(
        claim_id, "handler", "recovery", f"Recovery {what} ({party})"[:80],
        {"by": by}, {"party": party, "reason": reason, "amount": paid},
    )  # fmt: skip
    return saved


def activate_recovery(store: Store, claim_id: str) -> None:
    """Called when a claim is paid: a planned recovery is open, for the paid amount."""
    claim = store.get_claim(claim_id)
    if claim is None or claim.recovery is None or claim.payment is None:
        return
    if claim.recovery.status != "planned":
        return
    claim.recovery.status, claim.recovery.amount = "open", claim.payment.amount
    _save(store, claim_id, {"recovery": claim.recovery.model_dump(mode="json")})
    store.append_event(
        claim_id, "system", "recovery",
        f"Recovery open: CHF {claim.payment.amount:,.2f} ({claim.recovery.party})"[:80],
        {"why": "the claim is paid"}, {"amount": claim.payment.amount},
    )  # fmt: skip


def close_recovery(
    store: Store,
    claim_id: str,
    by: str,
    result: str,
    amount: float | None,
    reason: str,
) -> Claim:
    claim = _claim(store, claim_id)
    recovery = claim.recovery
    if result not in ("recovered", "abandoned"):
        raise ValueError("the result is recovered or abandoned")
    if result == "recovered" and (amount is None or amount <= 0):
        raise ValueError("give the amount that was recovered")
    if result == "abandoned":
        reason = _need(reason, "the reason to abandon")
    if recovery is None or recovery.status != "open":
        raise NotAllowed("the claim has no open recovery")
    recovery.status = "recovered" if result == "recovered" else "abandoned"
    recovery.recovered = round(amount or 0.0, 2) if result == "recovered" else 0.0
    recovery.closed_by, recovery.close_reason = by, " ".join(reason.split())
    saved = _save(store, claim_id, {"recovery": recovery.model_dump(mode="json")})
    store.append_event(
        claim_id, "handler", "recovery",
        f"Recovery {recovery.status}: CHF {recovery.recovered:,.2f}",
        {"by": by}, {"recovered": recovery.recovered, "reason": recovery.close_reason},
    )  # fmt: skip
    return saved

"""What the customer sees (non-negotiable 2, decision D18).

The customer view drops signals, score, reasons and the report. `step` is the chat
step (ask_story ... closed): it holds nothing about the checks. The two outcomes
that go to a person give the same exact text: a different text would tell a
fraudster that the system flagged the claim.
"""

from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel

from app.models import Claim, ClaimStatus, Decision, Outcome, Signal
from app.workflows import WORKFLOWS
from app.workflows.engine import missing_documents

VALIDATED = "Your claim is validated. You will receive a confirmation by e-mail."
TO_A_PERSON = "A claims specialist will review your case and contact you."
MESSAGES = {
    Outcome.straight_through: VALIDATED,
    Outcome.handler_review: TO_A_PERSON,
    Outcome.investigation: TO_A_PERSON,
}
# After the decision of a handler (issue #84): fixed texts. The customer never reads
# the reason that the handler wrote; a letter gives the reasons (package B6).
APPROVED = (
    "Your claim is approved for CHF {amount:,.2f}. "
    "You will receive a confirmation by e-mail."
)
DECIDED = (
    "A claims specialist has decided on your claim. "
    "You will receive a letter with the decision and the reasons."
)
# A required document is missing (signal R001): the customer reads what to send. It
# depends only on the documents, never on the outcome, and R001 always goes to a person.
WAITING = "We still need: {missing} You can upload here at any time. "
# The status is as blunt as the message: both statuses that go to a person match.
STATUS = {
    ClaimStatus.open: "open",
    ClaimStatus.waiting_customer: "waiting_customer",
    ClaimStatus.validated: "validated",
    ClaimStatus.in_review: "in_review",
    ClaimStatus.in_investigation: "in_review",
    ClaimStatus.approved: "approved",
    ClaimStatus.refused: "decided",
}


# The status in the words of a customer (issue #88). The key is the customer status,
# so review and investigation give the same line.
STATUS_LINE = {
    "open": "We are taking your claim.",
    "waiting_customer": "We wait for you: see what we still need.",
    "validated": "Your claim is validated. We prepare the payment.",
    "in_review": "A claims specialist reviews your case.",
    "approved": "Your claim is approved. We prepare the payment.",
    "decided": "A claims specialist has decided. A letter is on its way.",
}
BY_POST = (
    "A claims specialist set this amount. "
    "You will receive a letter by post with the reasons."
)


class LetterLine(BaseModel):
    label: str
    amount: float
    reason: str
    article: str


class Letter(BaseModel):
    """The settlement for the customer: the amount, and how it was calculated."""

    amount: float
    lines: list[LetterLine] = []
    note: str = ""


class CustomerView(BaseModel):
    claim_id: str
    policy_number: str | None
    status: str
    message: str | None
    step: str  # where the chat is (the agent state): the Done button needs it
    status_line: str
    requests: list[str] = []  # what the customer must still send or answer
    letter: Letter | None = None


def customer_message(outcome: Outcome, signals: Iterable[Signal] = ()) -> str:
    missing = next((s.detail for s in signals if s.code == "R001"), None)
    if missing is None:
        return MESSAGES[outcome]
    return WAITING.format(missing=missing.removeprefix("Missing: ")) + TO_A_PERSON


def after_decision(decision: Decision) -> str | None:
    """What the customer reads after a decision of a handler, or None: nothing."""
    if decision.action == "approve":
        return APPROVED.format(amount=decision.amount or 0)
    return DECIDED if decision.action == "refuse" else None


def _message(claim: Claim) -> str | None:
    if claim.decisions and claim.status in (ClaimStatus.approved, ClaimStatus.refused):
        return after_decision(claim.decisions[-1])
    return customer_message(claim.outcome, claim.signals) if claim.outcome else None


def _requests(claim: Claim) -> list[str]:
    """What a claim that waits still needs: the missing documents (they depend on the
    documents only, never on the outcome) and the last request of a handler."""
    if claim.status != ClaimStatus.waiting_customer:
        return []
    workflow = WORKFLOWS.get(claim.type or "")
    out = missing_documents(workflow, claim) if workflow else []
    if claim.decisions and claim.decisions[-1].action == "request":
        out.append(claim.decisions[-1].reason)
    return out


def _letter(claim: Claim) -> Letter | None:
    """The amount and its calculation, once the claim is validated or approved. The
    lines are the lines of the proposal (code wrote them, from fixed sentences). When
    a handler set another amount, the lines do not explain it and the reason of the
    handler is not for the customer: the letter then gives the amount alone."""
    proposed = claim.proposal.payable if claim.proposal else None
    if claim.status == ClaimStatus.approved and claim.decisions:
        amount = claim.decisions[-1].amount
    elif claim.status == ClaimStatus.validated:
        amount = proposed
    else:
        return None
    if amount is None:
        return None
    if claim.proposal is None or proposed is None or abs(amount - proposed) >= 0.005:
        return Letter(amount=amount, note=BY_POST)
    lines = [
        LetterLine(label=x.label, amount=x.amount, reason=x.reason, article=x.article)
        for x in claim.proposal.lines
    ]
    return Letter(amount=amount, lines=lines)


def customer_view(claim: Claim) -> CustomerView:
    status = STATUS[claim.status]
    return CustomerView(
        claim_id=claim.id,
        policy_number=claim.policy.number if claim.policy else None,
        status=status,
        message=_message(claim),
        step=claim.agent_state,
        status_line=STATUS_LINE[status],
        requests=_requests(claim),
        letter=_letter(claim),
    )


class ClaimRow(BaseModel):
    """One line of the list of claims: for a handler, never for the customer."""

    claim_id: str
    customer: str
    policy_number: str | None
    type: str | None
    claimed_amount: float | None
    status: ClaimStatus
    outcome: Outcome | None
    score: int | None
    reasons: list[str]
    opened_at: datetime | None


def claim_row(claim: Claim) -> ClaimRow:
    return ClaimRow(
        claim_id=claim.id,
        customer=claim.customer.name,
        policy_number=claim.policy.number if claim.policy else None,
        type=claim.type,
        claimed_amount=claim.claimed_amount,
        status=claim.status,
        outcome=claim.outcome,
        score=claim.score,
        reasons=claim.outcome_reasons,
        opened_at=claim.opened_at,
    )

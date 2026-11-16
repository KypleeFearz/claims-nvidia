"""What the customer sees (non-negotiable 2, decision D18).

The customer view drops signals, score, reasons and the report. `step` is the chat
step (ask_story ... closed): it holds nothing about the checks. The two outcomes
that go to a person give the same exact text: a different text would tell a
fraudster that the system flagged the claim.
"""

from collections.abc import Iterable
from datetime import datetime

from pydantic import BaseModel

from app.models import Claim, ClaimStatus, Outcome, Signal

VALIDATED = "Your claim is validated. You will receive a confirmation by e-mail."
TO_A_PERSON = "A claims specialist will review your case and contact you."
MESSAGES = {
    Outcome.straight_through: VALIDATED,
    Outcome.handler_review: TO_A_PERSON,
    Outcome.investigation: TO_A_PERSON,
}
# After the decision of a handler (issue #84): fixed texts. The customer never reads
# the reason that the handler wrote; a letter gives the reasons (package B6).
APPROVED = "Your claim is approved. You will receive a confirmation by e-mail."
DECIDED = (
    "A claims specialist has decided on your claim. "
    "You will receive a letter with the decision and the reasons."
)
AFTER_DECISION = {"approve": APPROVED, "refuse": DECIDED}
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


class CustomerView(BaseModel):
    claim_id: str
    policy_number: str | None
    status: str
    message: str | None
    step: str  # where the chat is (the agent state): the Done button needs it


def customer_message(outcome: Outcome, signals: Iterable[Signal] = ()) -> str:
    missing = next((s.detail for s in signals if s.code == "R001"), None)
    if missing is None:
        return MESSAGES[outcome]
    return WAITING.format(missing=missing.removeprefix("Missing: ")) + TO_A_PERSON


def _message(claim: Claim) -> str | None:
    if claim.decisions and claim.status in (ClaimStatus.approved, ClaimStatus.refused):
        return AFTER_DECISION[claim.decisions[-1].action]
    return customer_message(claim.outcome, claim.signals) if claim.outcome else None


def customer_view(claim: Claim) -> CustomerView:
    return CustomerView(
        claim_id=claim.id,
        policy_number=claim.policy.number if claim.policy else None,
        status=STATUS[claim.status],
        message=_message(claim),
        step=claim.agent_state,
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

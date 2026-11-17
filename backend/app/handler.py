"""The decisions of a handler (issue #84, package B3 of docs/BASE-ROADMAP.md).

The system routes a claim, and it never refuses one (non-negotiable 1). A person
does: this module is the only way to the statuses `approved` and `refused`, and it
asks for a known handler, and for a written reason to refuse.

What the customer reads (non-negotiable 2): a fixed text for approve and refuse,
never the reason of the handler; the text of a request, which passes the word filter
of the agent; and nothing at all for investigate and clear.
"""

from datetime import UTC, datetime

from app.agent.phrases import check_message
from app.investigation import conclusion, open_case
from app.lifecycle import IllegalMove
from app.models import Claim, ClaimStatus, Decision
from app.payment import set_reserve, settle
from app.store import Store
from app.third_parties import open_requests
from app.views import APPROVED as APPROVED  # re-export: the tests read them here
from app.views import DECIDED as DECIDED
from app.views import after_decision

# Two mock handlers. ponytail: a name in the request, no login (D33); a real desk
# needs accounts, and then the authority level of each handler.
HANDLERS = ["Sofia Brunner", "David Frei"]
TARGET: dict[str, ClaimStatus] = {
    "approve": ClaimStatus.approved,
    "refuse": ClaimStatus.refused,
    "request": ClaimStatus.waiting_customer,
    "investigate": ClaimStatus.in_investigation,
    # the two conclusions of an investigation (issue #92): the claim goes back to a
    # handler, who decides. `confirm` is not a refusal.
    "clear": ClaimStatus.in_review,
    "confirm": ClaimStatus.in_review,
}
CONCLUSION = {"clear": "not_confirmed", "confirm": "confirmed"}
NEEDS_REASON = {"refuse", "request", "investigate", "clear", "confirm"}


class BadDecision(ValueError):
    """The request of the handler is not complete or not allowed to be sent."""


def decide(
    store: Store,
    claim_id: str,
    action: str,
    by: str,
    reason: str,
    amount: float | None = None,
) -> Claim:
    """One decision of one handler. `KeyError`: unknown claim. `BadDecision`: the
    request is wrong. `IllegalMove`: the status of the claim does not allow it."""
    reason = reason.strip()
    if action not in TARGET:
        raise BadDecision(f"unknown action (known: {', '.join(TARGET)})")
    if by not in HANDLERS:
        raise BadDecision("unknown handler")
    if action in NEEDS_REASON and not reason:
        raise BadDecision(
            "write the text for the customer"
            if action == "request"
            else "a written reason is required"
        )
    if action == "request" and (why := check_message(reason, "no-model")):
        raise BadDecision(f"the customer must not read this text ({why})")
    claim = store.get_claim(claim_id)
    if claim is None:
        raise KeyError(claim_id)
    if claim.status == ClaimStatus.open:
        raise IllegalMove("the claim is still in the chat with the customer")
    if action in ("approve", "refuse") and (waits := open_requests(claim)):
        # the expert reports first (issue #102)
        raise IllegalMove(f"an expert request is open: {', '.join(waits)}")
    if action == "approve":
        amount = _approved_amount(claim, amount, reason)
    else:
        amount = None  # an amount belongs to an approval only
    decision = Decision.model_validate(
        {
            "action": action,
            "by": by,
            "reason": reason,
            "amount": amount,
            "time": datetime.now(UTC),
        }
    )
    more = (
        conclusion(claim, CONCLUSION[action], by, reason)
        if action in CONCLUSION
        else None
    )
    saved = store.decide(claim_id, TARGET[action], decision, more)
    assert saved is not None
    if action == "investigate":
        open_case(store, claim_id, reason)  # the plan of the investigator
    if action == "approve":  # issue #94: the reserve, then the mock payment
        set_reserve(store, claim_id, amount or 0.0, "approved")
        saved = settle(store, claim_id) or saved
    elif action == "refuse":
        set_reserve(store, claim_id, 0.0, "refused")
        saved = store.get_claim(claim_id) or saved
    text = reason if action == "request" else after_decision(decision)
    if text:  # investigate and clear: the customer sees nothing
        store.add_message(claim_id, "agent", text)
    return saved


def _approved_amount(claim: Claim, amount: float | None, reason: str) -> float:
    """The amount of an approval (issue #86): the proposal, or another amount with a
    written reason. With no proposal the handler must give the amount."""
    proposed = claim.proposal.payable if claim.proposal else None
    if amount is None:
        if proposed is None:
            raise BadDecision("no proposal for this claim: give the amount")
        return proposed
    if amount < 0:
        raise BadDecision("the amount cannot be below zero")
    if (proposed is None or abs(amount - proposed) >= 0.005) and not reason:
        raise BadDecision("another amount than the proposal needs a written reason")
    return round(amount, 2)

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
from app.lifecycle import IllegalMove
from app.models import Claim, ClaimStatus, Decision
from app.store import Store
from app.views import AFTER_DECISION
from app.views import APPROVED as APPROVED  # re-export: the tests read them here
from app.views import DECIDED as DECIDED

# Two mock handlers. ponytail: a name in the request, no login (D33); a real desk
# needs accounts, and then the authority level of each handler.
HANDLERS = ["Sofia Brunner", "David Frei"]
TARGET: dict[str, ClaimStatus] = {
    "approve": ClaimStatus.approved,
    "refuse": ClaimStatus.refused,
    "request": ClaimStatus.waiting_customer,
    "investigate": ClaimStatus.in_investigation,
    "clear": ClaimStatus.in_review,  # an investigation that found nothing
}
NEEDS_REASON = {"refuse", "request", "investigate", "clear"}


class BadDecision(ValueError):
    """The request of the handler is not complete or not allowed to be sent."""


def decide(store: Store, claim_id: str, action: str, by: str, reason: str) -> Claim:
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
    decision = Decision.model_validate(
        {"action": action, "by": by, "reason": reason, "time": datetime.now(UTC)}
    )
    saved = store.decide(claim_id, TARGET[action], decision)
    assert saved is not None
    text = reason if action == "request" else AFTER_DECISION.get(action)
    if text:  # investigate and clear: the customer sees nothing
        store.add_message(claim_id, "agent", text)
    return saved

"""The life of a claim (issue #72): which status may follow which.

`open` is the chat. The routing ends it: `validated`, `in_review` or
`in_investigation`, or `waiting_customer` when a required document is missing
(signal R001), whatever the outcome: the wait must say nothing about the outcome
(non-negotiable 2). A claim that waits opens again when the customer adds a document.
The moves of a handler (decide, pay, close) come with packages B3 and B7 of
`docs/BASE-ROADMAP.md`.
"""

from collections.abc import Iterable

from app.models import ClaimStatus, Outcome, Signal

S = ClaimStatus
MOVES: dict[ClaimStatus, set[ClaimStatus]] = {
    S.open: {S.waiting_customer, S.validated, S.in_review, S.in_investigation},
    S.waiting_customer: {S.open},
    S.validated: set(),
    S.in_review: set(),
    S.in_investigation: set(),
}
AFTER = {
    Outcome.straight_through: S.validated,
    Outcome.handler_review: S.in_review,
    Outcome.investigation: S.in_investigation,
}


class IllegalMove(ValueError):
    """A status move that `MOVES` does not allow."""


def check_move(before: ClaimStatus, after: ClaimStatus) -> None:
    if after not in MOVES[before]:
        raise IllegalMove(f"a claim cannot go from {before.value} to {after.value}")


def after_routing(outcome: Outcome, signals: Iterable[Signal]) -> ClaimStatus:
    if any(s.code == "R001" for s in signals):
        return S.waiting_customer
    return AFTER[outcome]

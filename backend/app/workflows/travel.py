"""Workflow C: travel (docs/WORKFLOWS.md)."""

from app.checks.by_line import r011
from app.checks.rules import (
    Rule,
    as_date,
    as_number,
    find_field,
    signal,
    sources,
)
from app.models import Claim, Signal
from app.workflows.engine import Question, RequiredDocument, Workflow


def required_documents(cause: str | None) -> list[RequiredDocument]:
    proof = ["cancellation_confirmation", "receipt"]
    if cause == "luggage":
        proof.append("luggage_report")  # the airline report of a lost bag
    docs: list[RequiredDocument] = [
        {
            "label": "a booking confirmation",
            "any_of": ["booking_confirmation"],
            "why": "it gives the price of the trip",
        },
        {
            "label": "a cancellation confirmation or a receipt",
            "any_of": proof,
            "why": "it gives what you lost or what was paid back",
        },
    ]
    if cause == "illness":
        docs.append(
            {
                "label": "a medical certificate",
                "any_of": ["medical_certificate"],
                "why": "it confirms that you could not travel",
            }
        )
    return docs


def cancellation_between_incident_and_departure(claim: Claim) -> Signal | None:
    cancelled = find_field(claim, "cancellation_date")
    departure = find_field(claim, "departure_date")
    c, d = as_date(cancelled), as_date(departure)
    if claim.incident_date is None or c is None or d is None:
        return None
    if claim.incident_date <= c <= d:
        return None
    return signal(
        "C002",
        f"Cancelled on {c}; it must be between the incident ({claim.incident_date}) "
        f"and the departure ({d}).",
        sources(cancelled, departure),
    )


def amount_is_price_minus_refund(claim: Claim) -> Signal | None:
    price_f = find_field(claim, "trip_price")
    refund_f = find_field(claim, "refund_received")
    price, refund = as_number(price_f), as_number(refund_f)
    if claim.claimed_amount is None or price is None or refund is None:
        return None
    if abs(claim.claimed_amount - (price - refund)) < 0.005:
        return None
    return signal(
        "C003",
        f"Claimed CHF {claim.claimed_amount:g}; the documents give CHF {price:g} "
        f"- CHF {refund:g} = CHF {price - refund:g}.",
        sources(price_f, refund_f),
    )


WORKFLOW = Workflow(
    claim_type="travel",
    required_documents=required_documents,
    straight_through_limit=2500,
    causes=(
        ("illness", r"\b(ill|sick|illness)\b"),
        ("luggage", r"\b(luggage|baggage)\b"),
    ),
    default_cause="cancellation",
    questions={
        "illness": (
            Question(
                "ill_who",
                "Who was ill: you, or a person who travels with you?",
                "The cover depends on who is ill.",
            ),
            Question(
                "ill_first_seen",
                "On what date did a doctor first see the illness?",
                "An illness known before the booking can be excluded.",
            ),
        ),
        "luggage": (
            Question(
                "luggage_reported",
                "Did you report the loss to the airline at the airport? "
                "Do you have a file number?",
                "The airline pays first; the report proves the loss.",
            ),
        ),
        "cancellation": (
            Question(
                "cancel_reason",
                "What made you cancel the trip?",
                "Only some reasons to cancel are covered.",
            ),
        ),
    },
    rules=[
        Rule("C002", cancellation_between_incident_and_departure),
        Rule("C003", amount_is_price_minus_refund),
        Rule("R011", r011),  # an item bought shortly before the loss (issue #109)
    ],
)

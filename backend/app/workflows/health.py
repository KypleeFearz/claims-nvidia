"""Workflow B: health / accident (docs/WORKFLOWS.md).

The own rules are signals of `checks/consistency.py`, not rules here, so that each
code has one place: "patient is the insured person" is C001 and "treatment dates are
on or after the incident date" is C002 (the consistency module gives both). Medical
text must never reach the application logs: this package logs nothing.
"""

from app.workflows.engine import Question, RequiredDocument, Workflow


def required_documents(cause: str | None) -> list[RequiredDocument]:
    return [
        {
            "label": "a medical invoice",
            "any_of": ["medical_invoice"],
            "why": "it gives the cost of the treatment",
        },
        {
            "label": "a medical report or a medical certificate",
            "any_of": ["medical_report", "medical_certificate"],
            "why": "it says what was treated and when",
        },
    ]


WORKFLOW = Workflow(
    claim_type="health",
    required_documents=required_documents,
    straight_through_limit=1000,
    expert_above=3000,
    causes=(
        (
            "accident",
            r"\b(accident|fell|fall|slipped|sprain\w*|twisted|injur\w*|crash\w*)\b",
        ),
        ("illness", r"\b(ill|sick|illness|disease|flu|infection)\b"),
    ),
    questions={
        "accident": (
            Question(
                "accident_where",
                "Where did the accident happen: at home, at work, on the road, "
                "at sport?",
                "An accident at work is for another insurer.",
            ),
            Question(
                "accident_other_person",
                "Was another person involved, or responsible?",
                "A liable person pays back: a recovery.",
            ),
        ),
        "illness": (
            Question(
                "illness_since",
                "When did the symptoms start?",
                "An accident policy does not cover an illness.",
            ),
        ),
    },
)

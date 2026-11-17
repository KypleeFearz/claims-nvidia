"""Workflow A: property / household (docs/WORKFLOWS.md).

The rule "police report if the cause is theft" is a required document, so R001
covers it. The rule "photo shows the declared damage" (C004) needs a vision model:
it is out of scope.
"""

from app.workflows.engine import Question, RequiredDocument, Workflow


def required_documents(cause: str | None) -> list[RequiredDocument]:
    docs: list[RequiredDocument] = [
        {
            "label": "a repair invoice or a repair quote",
            "any_of": ["repair_invoice", "repair_quote"],
            "why": "it gives the cost of the repair",
        },
        {
            "label": "a damage photo",
            "any_of": ["damage_photo"],
            "why": "it shows the damage",
        },
    ]
    if cause == "theft":
        docs.append(
            {
                "label": "a police report",
                "any_of": ["police_report"],
                "why": "a theft must be reported to the police",
            }
        )
    return docs


WORKFLOW = Workflow(
    claim_type="property",
    required_documents=required_documents,
    straight_through_limit=2000,
    expert_above=5000,
    causes=(
        ("water", r"\b(water|leak\w*|flood\w*)\b"),
        ("fire", r"\b(fire|smoke)\b"),
        ("theft", r"\b(theft|stolen|burglary)\b"),
        ("breakage", r"\b(broke|broken|cracked)\b"),
    ),
    questions={
        "water": (
            Question(
                "water_source",
                "Where did the water come from: a pipe, an appliance, the roof?",
                "A slow leak and water from outside can be excluded.",
            ),
            Question(
                "water_noticed",
                "When did you first see the damage?",
                "A late notice can make the damage larger.",
            ),
        ),
        "theft": (
            Question(
                "theft_entry",
                "How did the thief get in? Was a door or a window forced?",
                "A theft with no sign of entry is a known indicator.",
            ),
            Question(
                "theft_police",
                "Did you report it to the police? On what date?",
                "A theft needs a police report.",
            ),
        ),
        "fire": (
            Question(
                "fire_cause",
                "Do you know how the fire started?",
                "The cause decides the cover and a possible recovery.",
            ),
        ),
        "breakage": (
            Question(
                "breakage_how",
                "How did it break, and who was there?",
                "Another person can be liable for the damage.",
            ),
        ),
    },
)

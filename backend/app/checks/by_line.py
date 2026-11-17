"""Signals R011 to R013: indicators that belong to one line of business (issue #109,
package B4). Each is a rule of a workflow: the workflow file lists it in `rules`.

Pure functions of the claim and its extracted fields, as `checks/rules.py`. A rule
gives no signal when the field it needs is absent. A first reading of the practice.
"""

from datetime import timedelta

from app.checks.rules import as_date, as_number, signal
from app.models import Claim, Signal

NEW_ITEM_DAYS = 30
ROUND_TO = 500


def r011(claim: Claim) -> Signal | None:
    """An item bought shortly before the loss: on a receipt of the claim."""
    day = claim.incident_date
    if day is None:
        return None
    new = [
        f
        for d in claim.documents
        if d.document_type == "receipt"
        for f in d.fields
        if f.name == "purchase_date"
        and (bought := as_date(f)) is not None
        and day - timedelta(days=NEW_ITEM_DAYS) < bought <= day
    ]
    if not new:
        return None
    dates = ", ".join(sorted(str(f.value) for f in new))
    return signal(
        "R011",
        f"Bought on {dates}, less than {NEW_ITEM_DAYS} days before the loss on {day}.",
        [f.source for f in new],
    )


def r012(claim: Claim) -> Signal | None:
    """A medical invoice with a round total: providers bill odd amounts."""
    for d in claim.documents:
        if d.document_type != "medical_invoice":
            continue
        f = next((f for f in d.fields if f.name == "total"), None)
        total = as_number(f)
        if f is not None and total and total >= ROUND_TO and total % ROUND_TO == 0:
            return signal(
                "R012", f"The total is CHF {total:,.0f}, a round amount.", [f.source]
            )
    return None


def r013(claim: Claim) -> Signal | None:
    """A repair quote written before the damage happened."""
    if claim.incident_date is None:
        return None
    for d in claim.documents:
        f = next((f for f in d.fields if f.name == "quote_date"), None)
        quoted = as_date(f)
        if f is not None and quoted is not None and quoted < claim.incident_date:
            return signal(
                "R013",
                f"Quote dated {quoted}, before the incident on {claim.incident_date}.",
                [f.source],
            )
    return None

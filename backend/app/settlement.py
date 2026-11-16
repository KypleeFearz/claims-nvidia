"""The settlement proposal (issue #86, package B2 of docs/BASE-ROADMAP.md).

What the policy pays for a claim, line by line. Pure code, no model: a model finds
the facts in the documents, this module computes. Each line has an amount, a reason
in plain words, an article of the general conditions and, when the number comes
from a document, its source.

It is a proposal. `needs_person` says why a person must decide: the system pays
alone only what the contract alone gives (the loss, less the deductible and the
share of the customer). Any other reduction is a decision of a handler, because the
system never refuses a claim, in whole or in part (non-negotiable 1).

The rules are a first reading of the practice (open point O12 of docs/DECISIONS.md).
"""

import json
import os
from functools import cache
from pathlib import Path
from typing import Any

from app.checks.rules import as_number
from app.models import Claim, ExtractedField, Proposal, ProposalLine, Source
from app.store import DATA

CENT = 0.005
INVOICES = {
    "property": ("repair_invoice", "repair_quote"),
    "health": ("medical_invoice",),
}


@cache
def _wordings(path: str) -> dict[str, Any]:
    wordings: dict[str, Any] = json.loads(Path(path).read_text())
    return wordings


def _articles(line_of_business: str) -> tuple[str, dict[str, str]]:
    data = Path(os.environ.get("CLAIMS_DATA") or DATA) / "mock" / "policy_wordings.json"
    wording = _wordings(str(data)).get(line_of_business, {})
    return wording.get("name", ""), wording.get("articles", {})


def _field(claim: Claim, name: str, types: tuple[str, ...]) -> ExtractedField | None:
    for doc in claim.documents:
        if doc.document_type in types:
            for f in doc.fields:
                if f.name == name:
                    return f
    return None


def _documented(claim: Claim) -> tuple[float | None, str, list[Source], bool]:
    """The loss that the documents prove: the amount, how it was found, the sources,
    and whether the lines of an invoice give another amount than its total."""
    if claim.type == "travel" and claim.cause == "luggage":
        paid = [
            f
            for d in claim.documents
            if d.document_type == "receipt"
            for f in d.fields
            if f.name == "amount_paid" and as_number(f) is not None
        ]
        if not paid:
            return None, "", [], False
        parts = " + ".join(f"{as_number(f):,.2f}" for f in paid)
        return (
            sum(float(f.value) for f in paid),
            f"Receipts of the lost items: {parts}.",
            [f.source for f in paid],
            False,
        )
    if claim.type == "travel":
        price_f = _field(claim, "trip_price", ("booking_confirmation",))
        refund_f = _field(claim, "refund_received", ("cancellation_confirmation",))
        price, refund = as_number(price_f), as_number(refund_f) or 0.0
        if price is None or price_f is None:
            return None, "", [], False
        used = [f.source for f in (price_f, refund_f) if f is not None]
        how = f"Trip price {price:,.2f} less the refund {refund:,.2f}."
        return price - refund, how, used, False
    for doc in claim.documents:
        if doc.document_type in INVOICES.get(claim.type or "", ()):
            total_f = next((f for f in doc.fields if f.name == "total"), None)
            total = as_number(total_f)
            if total is None or total_f is None:
                continue
            kind = doc.document_type.replace("_", " ")
            items = [i.get("amount") for i in doc.line_items]
            amounts = [float(a) for a in items if isinstance(a, int | float)]
            if amounts and len(amounts) == len(items):
                lines = sum(amounts)
                if abs(lines - total) >= CENT:
                    how = (
                        f"The lines of the {kind} add up to {lines:,.2f}; "
                        f"its total says {total:,.2f}. The lines count."
                    )
                    return lines, how, [total_f.source], True
            return total, f"Total of the {kind}.", [total_f.source], False
    return None, "", [], False


def propose(claim: Claim) -> Proposal | None:
    """The proposal for a claim, or None when it has no policy yet."""
    policy = claim.policy
    if policy is None:
        return None
    wording, articles = _articles(policy.line_of_business)
    lines: list[ProposalLine] = []
    why: list[str] = []

    def line(label: str, amount: float, reason: str, key: str, at: Any = ()) -> None:
        lines.append(
            ProposalLine(
                label=label,
                amount=round(amount, 2),
                reason=reason,
                article=articles.get(key, ""),
                sources=list(at),
            )
        )

    documented, how, sources, lines_differ = _documented(claim)
    if documented is None:
        return Proposal(
            wording=wording,
            payable=None,
            needs_person=["No document gives the amount of the loss."],
        )
    line(
        "Documented loss", documented, how, "lines" if lines_differ else "loss", sources
    )
    if lines_differ:
        why.append("The lines of an invoice do not add up to its total.")
    amount = documented

    claimed = claim.claimed_amount
    if claimed is not None and claimed < amount - CENT:
        reason = f"The customer claims {claimed:,.2f}."
        line("Not claimed", claimed - amount, reason, "claimed")
        amount = claimed
    elif claimed is not None and claimed > amount + CENT:
        over = claimed - amount
        reason = (
            f"The customer claims {claimed:,.2f}: {over:,.2f} more than the documents."
        )
        line("Claimed, not documented", 0.0, reason, "documented")
        why.append("The claimed amount is above what the documents prove.")

    if policy.covered_causes and claim.cause not in policy.covered_causes:
        covered = ", ".join(policy.covered_causes)
        reason = f"The cause is {claim.cause}. The policy covers: {covered}."
        line("Cause not covered", -amount, reason, "cause")
        why.append(f"The cause ({claim.cause}) is not a covered cause of the policy.")
        amount = 0.0

    if policy.coinsurance and amount > 0:
        share = round(amount * policy.coinsurance, 2)
        reason = f"The customer pays {policy.coinsurance:.0%} of the cost."
        line("Share of the customer", -share, reason, "coinsurance")
        amount -= share
    if policy.deductible and amount > 0:
        taken = min(policy.deductible, amount)
        reason = f"Deductible of the policy: {policy.deductible:,.2f}."
        line("Deductible", -taken, reason, "deductible")
        amount -= taken
    if amount > policy.coverage_limit + CENT:
        reason = f"Coverage limit of the policy: {policy.coverage_limit:,.2f}."
        line(
            "Above the coverage limit", policy.coverage_limit - amount, reason, "limit"
        )
        why.append("The amount is above the coverage limit.")
        amount = policy.coverage_limit

    payable = round(max(amount, 0.0), 2)
    if payable < CENT and not why:
        why.append("The proposal is CHF 0.")
    return Proposal(wording=wording, lines=lines, payable=payable, needs_person=why)

"""Reserve, payee check, mock payment and export (issue #94, package B7).

- The reserve is the expected cost of a claim. It follows the claim: the claimed
  amount at first, the proposal after the routing, the approved amount, then zero.
- The payee check: the payout account must be known and must belong to the customer
  only. When it is not, the routing sends the claim to a handler.
- A claim that is validated or approved with an amount above zero is paid at once.
  No real payment and no real account: a record on the claim, and the status `paid`.
- A paid claim is exported: one JSON file for the mock claims system.

Nothing here decides: the amount comes from the proposal (a `straight_through`
claim) or from the decision of a handler.
"""

import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.checks.history import history, links
from app.lifecycle import PAYABLE
from app.models import Claim, ClaimStatus, Payment
from app.store import Store


def exports_dir() -> Path:
    return Path(os.environ.get("CLAIMS_EXPORTS", ".dev-data/exports"))


def payee_problem(claim: Claim) -> str | None:
    """Why the system must not pay this claim alone, or None."""
    person = history()["persons"].get(claim.customer.id)
    if person is None or not person.get("account"):
        return "No payout account is known for the customer."
    if any("account" in x["shared"] for x in links(claim.customer.id)):
        return "The payout account is also the account of another person."
    return None


def set_reserve(store: Store, claim_id: str, amount: float, why: str) -> None:
    claim = store.get_claim(claim_id)
    amount = round(max(amount, 0.0), 2)
    if claim is None or claim.reserve == amount:
        return
    store.update_claim(claim_id, {"reserve": amount})
    store.append_event(
        claim_id, "system", "reserve", f"Reserve: CHF {amount:,.2f} ({why})",
        {"why": why}, {"reserve": amount, "before": claim.reserve},
    )  # fmt: skip


def amount_to_pay(claim: Claim) -> float | None:
    """The approved amount, or the proposal of a claim the system validated alone."""
    if claim.status == ClaimStatus.validated:
        return claim.proposal.payable if claim.proposal else None
    approvals = [d for d in claim.decisions if d.action == "approve"]
    return approvals[-1].amount if approvals else None


def settle(store: Store, claim_id: str) -> Claim | None:
    """Pays a claim that is validated or approved, and exports it. An amount of zero
    or no amount: nothing is paid and the status stays."""
    claim = store.get_claim(claim_id)
    if claim is None or claim.status not in PAYABLE:
        return claim
    amount = amount_to_pay(claim)
    if not amount or amount <= 0:
        return claim
    person = history()["persons"].get(claim.customer.id) or {}
    payment = Payment(
        amount=round(amount, 2),
        account=str(person.get("account") or "unknown"),
        reference=f"PAY-{claim.id}",
        time=datetime.now(UTC),
    )
    paid = store.set_status(
        claim_id, ClaimStatus.paid, "system", f"mock payment {payment.reference}",
        {"payment": payment.model_dump(mode="json")},
    )  # fmt: skip
    set_reserve(store, claim_id, 0.0, "paid")
    paid = store.get_claim(claim_id)
    assert paid is not None
    path = export(paid)
    store.append_event(
        claim_id, "system", "export", f"Exported to the claims system: {path.name}",
        {"reference": payment.reference}, {"file": str(path), "amount": payment.amount},
    )  # fmt: skip
    return paid


class ExportRecord(BaseModel):
    """What the mock claims system receives for a paid claim: the schema of the file."""

    claim_id: str
    customer_id: str
    customer_name: str
    policy_number: str
    line_of_business: str
    claim_type: str
    cause: str | None
    incident_date: date | None
    claimed_amount: float | None
    outcome: str
    score: int
    signals: list[str]
    paid_amount: float
    payment_reference: str
    payout_account: str
    paid_at: datetime
    decided_by: str | None  # None: the system validated the claim alone
    proposal: list[dict[str, Any]]
    fields: list[dict[str, Any]]  # each with its document and page (non-negotiable 3)


def export_record(claim: Claim) -> ExportRecord:
    assert claim.policy and claim.payment and claim.outcome
    approvals = [d for d in claim.decisions if d.action == "approve"]
    return ExportRecord(
        claim_id=claim.id,
        customer_id=claim.customer.id,
        customer_name=claim.customer.name,
        policy_number=claim.policy.number,
        line_of_business=claim.policy.line_of_business,
        claim_type=claim.type or "unknown",
        cause=claim.cause,
        incident_date=claim.incident_date,
        claimed_amount=claim.claimed_amount,
        outcome=claim.outcome.value,
        score=claim.score or 0,
        signals=[s.code for s in claim.signals],
        paid_amount=claim.payment.amount,
        payment_reference=claim.payment.reference,
        payout_account=claim.payment.account,
        paid_at=claim.payment.time,
        decided_by=approvals[-1].by if approvals else None,
        proposal=[
            {"label": x.label, "amount": x.amount, "article": x.article}
            for x in (claim.proposal.lines if claim.proposal else [])
        ],
        fields=[
            {
                "document_type": d.document_type,
                "name": f.name,
                "value": f.value,
                "document_id": f.source.document_id,
                "page": f.source.page,
            }
            for d in claim.documents
            for f in d.fields
        ],
    )


def export(claim: Claim) -> Path:
    folder = exports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{claim.id}.json"  # the id is ours: CL- and eight hex characters
    path.write_text(export_record(claim).model_dump_json(indent=2))
    return path

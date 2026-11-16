"""Signals R006 to R010: the history of the customer and the links between persons
(issue #90, package B4 of docs/BASE-ROADMAP.md).

Most fraud does not show in one document. It shows in what the same person claimed
before, and in what two persons share: a payout account, a phone, an address, a
provider. These signals read `data/mock/history.json` and the claim. They are code,
with no model, and they give no evidence in a document: the detail names the earlier
claim or the other person.

ponytail: the history is one static mock file; the claims made in the demo do not
count as history. A real system reads the claims database and a party register.
"""

import json
import os
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from app.checks.rules import signal
from app.models import Claim, Signal
from app.store import DATA

MANY_CLAIMS = 3
PROVIDER_FIELDS = ("repair_company", "provider")


@cache
def _load(path: str) -> dict[str, Any]:
    file = Path(path)
    data: dict[str, Any] = json.loads(file.read_text()) if file.is_file() else {}
    return {"persons": data.get("persons", {}), "claims": data.get("claims", [])}


def history() -> dict[str, Any]:
    data = Path(os.environ.get("CLAIMS_DATA") or DATA) / "mock" / "history.json"
    return _load(str(data))


def _norm(text: object) -> str:
    return " ".join(str(text).split()).casefold()


def _fraud_claims(person: str) -> list[dict[str, Any]]:
    return [
        c
        for c in history()["claims"]
        if c["person"] == person and c["result"] == "fraud"
    ]


def earlier_claims(customer_id: str) -> list[dict[str, Any]]:
    return [c for c in history()["claims"] if c["person"] == customer_id]


def links(customer_id: str) -> list[dict[str, Any]]:
    """The other persons who share the account, the phone or the address of this
    customer, with what they share and their claims closed as fraud."""
    persons = history()["persons"]
    me = persons.get(customer_id)
    if me is None:
        return []
    out = []
    for other_id, other in persons.items():
        shared = [
            key
            for key in ("account", "phone", "address")
            if other_id != customer_id and _norm(other[key]) == _norm(me[key])
        ]
        if shared:
            out.append(
                {
                    "person": other_id,
                    "name": other["name"],
                    "shared": shared,
                    "fraud_claims": [c["id"] for c in _fraud_claims(other_id)],
                }
            )
    return out


def profile(customer_id: str) -> dict[str, Any]:
    """What a handler reads on the case page: the contact data of the customer, his
    earlier claims, and each linked person with what is shared and his claims."""
    return {
        "person": history()["persons"].get(customer_id),
        "earlier_claims": earlier_claims(customer_id),
        "links": [
            {**x, "claims": earlier_claims(x["person"])} for x in links(customer_id)
        ],
    }


def r006(claim: Claim) -> Signal | None:
    day = claim.incident_date
    if day is None:
        return None
    recent = [
        c
        for c in earlier_claims(claim.customer.id)
        if day - timedelta(days=365) <= date.fromisoformat(c["date"]) <= day
    ]
    if len(recent) < MANY_CLAIMS:
        return None
    ids = ", ".join(c["id"] for c in recent)
    return signal("R006", f"{len(recent)} earlier claims in 12 months: {ids}.")


def r007(claim: Claim) -> Signal | None:
    fraud = _fraud_claims(claim.customer.id)
    if not fraud:
        return None
    ids = ", ".join(f"{c['id']} ({c['date']})" for c in fraud)
    return signal("R007", f"Earlier claim closed as fraud: {ids}.")


def r008(claim: Claim) -> Signal | None:
    same = [x for x in links(claim.customer.id) if "account" in x["shared"]]
    if not same:
        return None
    who = ", ".join(_with_fraud(x) for x in same)
    return signal("R008", f"The payout account is also the account of {who}.")


def r009(claim: Claim) -> Signal | None:
    same = [
        x
        for x in links(claim.customer.id)
        if x["fraud_claims"] and {"phone", "address"} & set(x["shared"])
    ]
    if not same:
        return None
    what = ", ".join(
        f"the {' and the '.join(k for k in x['shared'] if k != 'account')} "
        f"of {_with_fraud(x)}"
        for x in same
    )
    return signal("R009", f"The customer has {what}.")


def r010(claim: Claim) -> Signal | None:
    by_provider: dict[str, list[str]] = {}
    for c in history()["claims"]:
        if c["result"] == "fraud" and c.get("provider"):
            by_provider.setdefault(_norm(c["provider"]), []).append(c["id"])
    found = [
        f
        for d in claim.documents
        for f in d.fields
        if f.name in PROVIDER_FIELDS and _norm(f.value) in by_provider
    ]
    if not found:
        return None
    f = found[0]
    ids = ", ".join(by_provider[_norm(f.value)])
    return signal(
        "R010",
        f"{f.value} is the provider of claims closed as fraud: {ids}.",
        [f.source],
    )


def _with_fraud(link: dict[str, Any]) -> str:
    who = f"{link['name']} ({link['person']})"
    if link["fraud_claims"]:
        who += f", who has claims closed as fraud: {', '.join(link['fraud_claims'])}"
    return who


CHECKS = (r006, r007, r008, r009, r010)


def check_history(claim: Claim) -> list[Signal]:
    return [s for check in CHECKS if (s := check(claim)) is not None]

"""Business numbers (issue #96, package B9 of docs/BASE-ROADMAP.md).

Two things, both for the insurer, never for the customer:

- `live`: what the claims of this demo say: how many passed with no person, how
  long a claim took, what was claimed, proposed and paid, how the investigations
  ended.
- `play_kit`: the stories of the mock kit, played through the active reader, the
  checks, the routing and the settlement, then compared with what the kit expects
  (`data/mock/index.json`) and with the amounts calculated by hand
  (`data/mock/settlements.json`). No agent model runs: the story comes from the
  index. It uses a store of its own, in memory: the handler queue does not see
  these claims and no export file is written.

The evaluation harness of the intern (issue #19) measures the reading, field by
field. The kit run measures what a reading error costs: a wrong outcome, a missed
fraud, a wrong payment.
"""

import json
import tempfile
import threading
from io import BytesIO
from pathlib import Path
from statistics import median
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pypdf import PdfReader

from app import demo
from app.docintel.reader import Reader
from app.models import Claim, ClaimStatus, Document, Outcome
from app.store import Store
from app.tools import build_gateway

router = APIRouter(prefix="/numbers")
_one_run = threading.Lock()  # a kit run can call a model 26 times: one at a time
CHECKS = ("run_checks", "check_tampering", "check_consistency", "assess_and_route")


def _share(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def _minutes(claim: Claim) -> float | None:
    """From the first notice to the end: the payment, or the last decision of a
    claim that a handler refused."""
    if claim.opened_at is None:
        return None
    if claim.payment is not None:
        end = claim.payment.time
    elif claim.status == "refused" and claim.decisions:  # read only: no enum here,
        # a test checks that only the handler module names the decided statuses
        end = claim.decisions[-1].time
    else:
        return None
    return round((end - claim.opened_at).total_seconds() / 60, 1)


def _recoveries(claims: list[Claim]) -> dict[str, Any]:
    found = [c.recovery for c in claims if c.recovery is not None]
    is_open = [r for r in found if r.status == "open"]
    back = [r for r in found if r.status == "recovered"]
    return {
        "open": len(is_open),
        "open_amount": round(sum(r.amount or 0 for r in is_open), 2),
        "recovered": len(back),
        "recovered_amount": round(sum(r.recovered or 0 for r in back), 2),
    }


def live(claims: list[Claim]) -> dict[str, Any]:
    routed = [c for c in claims if c.outcome is not None]
    alone = [c for c in routed if c.outcome == Outcome.straight_through]
    ended = [m for c in claims if (m := _minutes(c)) is not None]
    concluded = [
        c.investigation.conclusion.result
        for c in claims
        if c.investigation and c.investigation.conclusion
    ]
    actions: dict[str, int] = {}
    for c in claims:
        for d in c.decisions:
            actions[d.action] = actions.get(d.action, 0) + 1
    return {
        "claims": len(claims),
        "by_status": {
            s.value: n for s in ClaimStatus if (n := sum(c.status == s for c in claims))
        },
        "routed": len(routed),
        "straight_through": len(alone),
        "straight_through_share": _share(len(alone), len(routed)),
        "to_a_person": len(routed) - len(alone),
        "ended": len(ended),
        "median_minutes_to_end": median(ended) if ended else None,
        "claimed": round(sum(c.claimed_amount or 0 for c in routed), 2),
        "proposed": round(
            sum((c.proposal.payable or 0) if c.proposal else 0 for c in routed), 2
        ),
        "paid": round(sum(c.payment.amount for c in claims if c.payment), 2),
        "open_reserve": round(sum(c.reserve or 0 for c in claims), 2),
        "investigations": {
            "open": sum(c.status == ClaimStatus.in_investigation for c in claims),
            "confirmed": concluded.count("confirmed"),
            "not_confirmed": concluded.count("not_confirmed"),
            "not_confirmed_share": _share(
                concluded.count("not_confirmed"), len(concluded)
            ),
        },
        "decisions": actions,
        "experts_open": sum(1 for c in claims for e in c.experts if e.report is None),
        "recoveries": _recoveries(claims),
    }


def _play(
    gateway: Any, uploads: Path, scenario: dict[str, Any], story: dict[str, Any]
) -> Claim:
    """One story with no agent: the facts of the index, the documents through the
    reader, then the three checks and the routing, in the order of the graph."""
    store: Store = gateway.store
    claim = store.create_claim(scenario["customer_id"])
    assert claim is not None
    cid = claim.id
    store.set_claim_policy(cid, scenario["policy"])
    facts = {
        "claim_id": cid,
        "description": story["text"],
        "incident_date": story["incident_date"],
        "claimed_amount": story["amount"],
        "cause": story["cause"],
    }
    gateway.call(cid, "update_claim", facts)
    for i, doc in enumerate(scenario["documents"]):
        path = demo._kit()[(scenario["folder"], doc["file"])]
        data = path.read_bytes()
        pages = len(PdfReader(BytesIO(data)).pages) if path.suffix == ".pdf" else 1
        file = f"{cid}/D-{i}{path.suffix}"
        (uploads / cid).mkdir(parents=True, exist_ok=True)
        (uploads / file).write_bytes(data)
        document = Document(
            id=f"D-{cid}-{i}",
            file=file,
            pages=pages,
            file_metadata={"original_name": doc["file"]},
        )
        store.save_document(cid, document)
        for tool in ("classify_document", "extract_document"):
            gateway.call(cid, tool, {"document_id": document.id})
    for tool in CHECKS:
        gateway.call(cid, tool, {"claim_id": cid})
    played = store.get_claim(cid)
    assert played is not None
    return played


def play_kit(reader: Reader) -> dict[str, Any]:
    index = demo._index()
    by_hand: dict[str, Any] = json.loads(
        (demo._data() / "mock" / "settlements.json").read_text()
    )
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(":memory:")
        store.export_files = False  # a kit run pays nobody and exports nothing
        gateway = build_gateway(store, reader, Path(tmp))
        for scenario in index.get("scenarios", []):
            for story in scenario["stories"]:
                key = f"{scenario['folder']}/{story['amount']:g}"
                expected_pay = by_hand.get(key, {}).get("payable")
                row: dict[str, Any] = {
                    "story": key,
                    "title": scenario["title"],
                    "expected_outcome": story["outcome"],
                    "expected_signals": sorted(story["signals"]),
                    "expected_payable": expected_pay,
                }
                try:
                    claim = _play(gateway, Path(tmp), scenario, story)
                    payable = claim.proposal.payable if claim.proposal else None
                    row |= {
                        "outcome": claim.outcome.value if claim.outcome else None,
                        "signals": sorted(s.code for s in claim.signals),
                        "payable": payable,
                        "error": None,
                    }
                except Exception as e:  # a reader in progress fails: show it
                    row |= {
                        "outcome": None,
                        "signals": [],
                        "payable": None,
                        "error": f"{type(e).__name__}: {e}"[:300],
                    }
                row["outcome_right"] = row["outcome"] == row["expected_outcome"]
                row["signals_right"] = row["signals"] == row["expected_signals"]
                row["payable_right"] = (
                    row["payable"] is not None
                    and expected_pay is not None
                    and abs(row["payable"] - expected_pay) < 0.005
                )
                rows.append(row)
    fraud = [r for r in rows if r["expected_outcome"] == "investigation"]
    clean = [r for r in rows if r["expected_outcome"] != "investigation"]
    return {
        "reader": reader.name,
        "stories": len(rows),
        "outcomes_right": sum(r["outcome_right"] for r in rows),
        "signals_right": sum(r["signals_right"] for r in rows),
        "amounts_right": sum(r["payable_right"] for r in rows),
        "seeded_fraud": len(fraud),
        "fraud_caught": sum(r["outcome"] == "investigation" for r in fraud),
        "fraud_missed": sum(r["outcome"] != "investigation" for r in fraud),
        "false_alarms": sum(r["outcome"] == "investigation" for r in clean),
        "rows": rows,
    }


@router.get("")
def numbers(request: Request) -> dict[str, Any]:
    store: Store = request.app.state.store
    return live(store.list_claims())


@router.post("/kit")
def kit(request: Request) -> dict[str, Any]:
    if not _one_run.acquire(blocking=False):
        raise HTTPException(409, "a kit run is in progress")
    try:
        return play_kit(request.app.state.reader)
    finally:
        _one_run.release()

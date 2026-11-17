"""Signals R011 to R013 (issue #109): indicators of one line of business."""

from datetime import date
from typing import Any

from app.checks.rules import run_rules
from app.fraud.report import WHY
from app.investigation import TASKS
from app.models import Claim
from app.workflows import WORKFLOWS
from tests.builders import document, travel_claim


def codes(claim: Claim, workflow: str) -> list[str]:
    return [s.code for s in run_rules(claim, WORKFLOWS[workflow])]


def claim(kind: str, cause: str, docs: list[Any], **more: Any) -> Claim:
    base = {"type": kind, "cause": cause, "documents": docs}
    return travel_claim(**{**base, "incident_date": date(2026, 5, 14), **more})


def receipt(doc_id: str, bought: str) -> Any:
    return document(doc_id, "receipt", {"purchase_date": bought, "amount_paid": 300.0})


def test_each_workflow_lists_its_own_rules() -> None:
    by = {k: [r.code for r in w.rules] for k, w in WORKFLOWS.items()}
    assert by["travel"] == ["C002", "C003", "R011"]
    assert by["property"] == ["R011", "R013"]
    assert by["health"] == ["R012"]
    for code in ("R011", "R012", "R013"):
        assert code in WHY and code in TASKS  # the report and the plan know them


def test_an_item_bought_less_than_30_days_before_the_loss() -> None:
    docs = [receipt("D-1", "2026-05-02"), receipt("D-2", "2026-03-18")]
    [s] = [
        s
        for s in run_rules(claim("travel", "luggage", docs), WORKFLOWS["travel"])
        if s.code == "R011"
    ]
    assert "2026-05-02" in s.detail and "2026-03-18" not in s.detail
    assert [e.document_id for e in s.evidence] == ["D-1"] and s.points == 15
    old = [receipt("D-1", "2026-04-14"), receipt("D-2", "2026-03-18")]  # 30 days: no
    assert "R011" not in codes(claim("travel", "luggage", old), "travel")
    after = [receipt("D-1", "2026-05-20")]  # bought after the loss: another matter
    assert "R011" not in codes(claim("travel", "luggage", after), "travel")
    assert "R011" in codes(claim("property", "theft", docs), "property")
    assert "R011" not in codes(claim("health", "accident", docs), "health")


def test_a_round_total_on_a_medical_invoice() -> None:
    def invoice(total: float) -> list[Any]:
        return [document("D-inv", "medical_invoice", {"total": total})]

    for total, fires in ((1500.0, True), (500.0, True), (1450.0, False), (0.0, False)):
        found = "R012" in codes(claim("health", "accident", invoice(total)), "health")
        assert found == fires, total
    repair = [document("D-inv", "repair_invoice", {"total": 1500.0})]
    assert "R012" not in codes(claim("health", "accident", repair), "health")


def test_a_repair_quote_dated_before_the_incident() -> None:
    def quote(day: str) -> list[Any]:
        return [document("D-q", "repair_quote", {"quote_date": day, "total": 900.0})]

    early = claim("property", "water", quote("2026-05-10"))
    [s] = [s for s in run_rules(early, WORKFLOWS["property"]) if s.code == "R013"]
    assert s.points == 20 and s.evidence[0].document_id == "D-q"
    same_day = claim("property", "water", quote("2026-05-14"))
    assert "R013" not in codes(same_day, "property")
    no_date = claim("property", "water", quote("2026-05-10"), incident_date=None)
    assert "R013" not in codes(no_date, "property")

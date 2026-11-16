"""Tools `run_checks`, `assess_and_route` and `write_fraud_report`: no model.

`assess_and_route` is the only place that decides the outcome, and the agent gets
the customer message from it, nothing else. For `investigation` it calls
`write_fraud_report` itself (trusted code, not the agent), which computes the same
outcome from the claim and saves outcome and report together, in one store call
(non-negotiable 4). If the report fails, `assess_and_route` fails and nothing is
saved. The report tool is called inside `assess_and_route`, so its event is written
first (it ends first): "Write fraud report" comes right before "Assess and route".
"""

from typing import Any

from app.checks.history import check_history
from app.checks.rules import run_rules
from app.fraud.report import write_report
from app.fraud.routing import route
from app.fraud.routing import score as sum_points
from app.investigation import open_case
from app.models import Claim, Outcome
from app.payment import set_reserve, settle
from app.settlement import propose
from app.tools.gateway import Gateway
from app.views import customer_message
from app.workflows import WORKFLOWS


def register(gateway: Gateway) -> None:
    def load(claim_id: str) -> Claim:
        claim = gateway.store.get_claim(claim_id)
        if claim is None:
            raise KeyError(f"unknown claim: {claim_id}")
        return claim

    def run_checks(claim_id: str) -> dict[str, Any]:
        claim = load(claim_id)
        signals = run_rules(claim, WORKFLOWS.get(claim.type or ""))
        signals += check_history(claim)  # R006 to R010: history and links
        dumped = [s.model_dump(mode="json") for s in signals]
        gateway.store.update_claim(claim_id, {"signals": dumped})
        return {"signals": dumped}

    def assess_and_route(claim_id: str) -> dict[str, Any]:
        claim = load(claim_id)
        total = sum_points(claim.signals)
        outcome, reasons = route(claim, claim.signals, WORKFLOWS.get(claim.type or ""))
        proposal = propose(claim)  # code, no model: the handler approves it
        if outcome == Outcome.investigation:
            # its own event (rule 5); it saves the outcome and the report together
            gateway.call(claim_id, "write_fraud_report", {"claim_id": claim_id})
        else:
            gateway.store.save_routing(
                claim_id, total, outcome, reasons, proposal=proposal
            )
        _reserve_and_pay(claim_id)
        return {
            "score": total,
            "outcome": outcome.value,
            "reasons": reasons,
            "proposal": proposal.model_dump(mode="json") if proposal else None,
            "customer_message": customer_message(outcome, claim.signals),
        }

    def _reserve_and_pay(claim_id: str) -> None:
        """After the routing (issue #94): the reserve follows the proposal, and a claim
        that the system validated alone is paid at once."""
        routed = load(claim_id)
        payable = routed.proposal.payable if routed.proposal else None
        expected = payable if payable is not None else routed.claimed_amount
        set_reserve(gateway.store, claim_id, expected or 0.0, "routing")
        settle(gateway.store, claim_id)

    def write_fraud_report(claim_id: str) -> dict[str, Any]:
        """Writes the report and saves it with the outcome, score and reasons that
        the routing gives for the signals of the claim. Nothing comes from a caller."""
        claim = load(claim_id)
        outcome, reasons = route(claim, claim.signals, WORKFLOWS.get(claim.type or ""))
        if outcome != Outcome.investigation:
            raise ValueError(
                "a fraud report is only for a claim routed to investigation"
            )
        total = sum_points(claim.signals)
        report = write_report(claim, claim.signals, outcome, total)
        gateway.store.save_routing(
            claim_id, total, outcome, reasons, report, propose(claim)
        )
        open_case(gateway.store, claim_id)  # the plan of the investigator (#92)
        return report.model_dump(mode="json")

    # The agent gets nothing from the checks, and only the customer text from the
    # routing. The event keeps the full result for the pipeline panel.
    gateway.register(
        "run_checks",
        run_checks,
        visible=(),
        description=(
            "Check step 1 of 3. Run it only when the customer says they are done "
            "and have no more documents."
        ),
    )
    gateway.register(
        "assess_and_route",
        assess_and_route,
        visible=("customer_message",),
        description=(
            "The last step. Call it after run_checks, check_tampering and "
            "check_consistency. Returns the message for the customer."
        ),
    )
    gateway.register(
        "write_fraud_report",
        write_fraud_report,
        visible=(),
        title="Write fraud report (template)",
    )

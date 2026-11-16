"""Pydantic models of docs/ARCHITECTURE.md section 4."""

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

LineOfBusiness = Literal["Travel", "Household", "Accident"]
ClaimType = Literal["travel", "property", "health"]


class Outcome(StrEnum):
    """No rejection (non-negotiable 1): only these three outcomes exist."""

    straight_through = "straight_through"
    handler_review = "handler_review"
    investigation = "investigation"


class ClaimStatus(StrEnum):
    """Where a claim is in its life. The allowed moves are in `app/lifecycle.py`."""

    open = "open"  # the customer and the agent build the file
    waiting_customer = "waiting_customer"  # routed, but a required document is missing
    validated = "validated"
    in_review = "in_review"  # a handler has the case
    in_investigation = "in_investigation"
    approved = "approved"  # a handler decided (app/handler.py), never the system
    refused = "refused"  # a handler decided, with a written reason
    paid = "paid"  # the mock payment is made and the claim is exported (issue #94)


class Customer(BaseModel):
    id: str
    name: str


class Policy(BaseModel):
    number: str
    customer_id: str
    line_of_business: LineOfBusiness
    start: date
    end: date
    coverage_limit: float
    # the terms (issue #86); the defaults: a policy with no terms pays the whole loss
    deductible: float = 0
    coinsurance: float = Field(default=0, ge=0, le=1)  # the share the customer pays
    covered_causes: list[str] = []  # empty: every cause


class Source(BaseModel):
    """Where a value was read: document, page (1 or more), snippet or box."""

    document_id: str
    page: int = Field(ge=1)
    snippet: str | None = None
    box: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1

    @model_validator(mode="after")
    def _snippet_or_box(self) -> Self:
        if not self.snippet and self.box is None:
            raise ValueError("a source needs a snippet or a box")
        return self


class ExtractedField(BaseModel):
    """Non-negotiable 3: `source` is required, a field with no source is refused."""

    name: str
    value: Any
    confidence: float = Field(ge=0, le=1)
    source: Source


class DocumentType(StrEnum):
    booking_confirmation = "booking_confirmation"
    cancellation_confirmation = "cancellation_confirmation"
    medical_certificate = "medical_certificate"
    repair_invoice = "repair_invoice"
    repair_quote = "repair_quote"
    damage_photo = "damage_photo"
    police_report = "police_report"
    medical_invoice = "medical_invoice"
    medical_report = "medical_report"
    receipt = "receipt"
    luggage_report = "luggage_report"
    unknown = "unknown"


class Document(BaseModel):
    id: str
    file: str
    document_type: str = "unknown"
    pages: int = Field(default=1, ge=1)
    fields: list[ExtractedField] = []
    line_items: list[dict[str, Any]] = []
    file_metadata: dict[str, Any] = {}


class Extraction(BaseModel):
    """What a Reader returns for one document (the contract of extract_document)."""

    document_type: DocumentType
    fields: list[ExtractedField] = []
    line_items: list[dict[str, Any]] = []


class Signal(BaseModel):
    code: str
    category: Literal["rule", "tampering", "consistency"]
    severity: Literal["low", "medium", "high"]
    points: int
    title: str
    detail: str  # what the system saw
    expected: str = ""  # what it expected: the same text for every signal of a code
    evidence: list[Source] = []


class FraudFinding(BaseModel):
    """One signal in the report: what was seen, where, and what was expected."""

    code: str
    title: str
    saw: str
    where: list[str]  # "file name, page N"; a plain note when no document holds it
    expected: str


class FraudReport(BaseModel):
    """Written for every `investigation` (docs/FRAUD.md), from the signals only."""

    outcome: Outcome
    score: int
    summary: str  # one plain sentence that says why
    findings: list[FraudFinding] = []
    not_checked: list[str] = []
    next_steps: list[str] = []


class PipelineEvent(BaseModel):
    sequence: int
    time: datetime
    actor: Literal["agent", "tool", "workflow", "sandbox", "system", "handler"]
    stage: str
    title: str
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}
    model: str | None = None
    duration: float | None = None  # seconds


class SandboxRecord(BaseModel):
    """The OpenShell sandbox of a claim (`CLAIMS_MODE=sandbox`). The token itself is
    never stored: only its SHA-256, which is enough to check a request."""

    claim_id: str
    name: str
    token_sha256: str
    policy_file: str
    created: datetime
    closed: bool = False


class ProposalLine(BaseModel):
    label: str
    amount: float  # CHF; a reduction is negative
    reason: str
    article: str = ""  # of the general conditions (data/mock/policy_wordings.json)
    sources: list[Source] = []


class Proposal(BaseModel):
    """What the policy pays, line by line (issue #86). A handler approves it."""

    wording: str = ""
    lines: list[ProposalLine] = []
    payable: float | None = None  # None: no document gives the loss
    needs_person: list[str] = []  # why the system must not pay this alone


class Decision(BaseModel):
    """What a handler did on a claim, and why (issue #84)."""

    action: Literal["approve", "refuse", "request", "investigate", "clear", "confirm"]
    by: str
    reason: str = ""
    amount: float | None = None  # CHF approved, for `approve`
    time: datetime


class NoticeItem(BaseModel):
    """One question of the first notice (issue #98), with its answer when given."""

    key: str
    question: str
    why: str
    answer: str | None = None


class Payment(BaseModel):
    """A mock payment (issue #94): no real money, no real account."""

    amount: float
    account: str
    reference: str
    time: datetime


class InvestigationTask(BaseModel):
    id: str
    text: str
    codes: list[str] = []  # the signals this task answers
    done: bool = False
    by: str | None = None
    note: str = ""
    time: datetime | None = None


class Note(BaseModel):
    by: str
    text: str
    time: datetime


class Conclusion(BaseModel):
    result: Literal["confirmed", "not_confirmed"]
    by: str
    text: str
    time: datetime


class Investigation(BaseModel):
    """The case of an investigator (issue #92): a plan, an evidence log, a
    conclusion. For the handler desk only: the customer view never reads it."""

    tasks: list[InvestigationTask] = []
    notes: list[Note] = []
    conclusion: Conclusion | None = None


class Claim(BaseModel):
    id: str
    customer: Customer
    policy: Policy | None = None
    type: ClaimType | None = None
    description: str | None = None
    incident_date: date | None = None
    claimed_amount: float | None = None
    cause: str | None = None  # travel: illness, luggage or cancellation
    agent_state: str = "ask_story"
    documents: list[Document] = []
    signals: list[Signal] = []
    score: int | None = None
    outcome: Outcome | None = None
    outcome_reasons: list[str] = []
    fraud_report: FraudReport | None = None
    proposal: Proposal | None = None
    status: ClaimStatus = ClaimStatus.open
    notice: list[NoticeItem] = []  # the questions for the cause, and the answers
    decisions: list[Decision] = []
    investigation: Investigation | None = None
    reserve: float | None = (
        None  # the expected cost; for the insurer, never the customer
    )
    payment: Payment | None = None
    opened_at: datetime | None = None  # None: a claim made before issue #84


class Message(BaseModel):
    role: Literal["customer", "agent"]
    text: str
    time: datetime

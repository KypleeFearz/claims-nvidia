"""One schema per document type (issue #17): the form that a reader fills.

A schema holds values only. The source and the confidence stay in
`ExtractedField` (non-negotiable 3).

Every field is a required key. `null` means "not found on the document" (D77).
Types are strict and an unknown key is refused.
Numbers must be finite. Text is stripped and never empty: blank text is `null`.

Validate JSON text with `model_validate_json`. In strict mode, `model_validate`
on a Python dict refuses a date string.

To add a type: one `DocumentType` value, one class and one `SCHEMAS` entry.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict

from app.models import DocumentType


class _Schema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        allow_inf_nan=False,
        str_strip_whitespace=True,
        str_min_length=1,
    )


class LineItem(_Schema):
    description: str | None
    quantity: float | None
    unit_price: float | None
    amount: float | None


class CancellationLine(_Schema):
    description: str | None
    amount: float | None  # can be negative: a fee taken from the refund


class BookingConfirmation(_Schema):
    booking_reference: str | None
    traveller_name: str | None
    departure_date: date | None
    trip_price: float | None
    line_items: list[LineItem]


class CancellationConfirmation(_Schema):
    booking_reference: str | None
    cancellation_date: date | None
    refund_received: float | None
    line_items: list[CancellationLine]


class MedicalCertificate(_Schema):
    patient_name: str | None
    cause: str | None
    unfit_from: date | None


class RepairInvoice(_Schema):
    invoice_number: str | None
    invoice_date: date | None
    repair_company: str | None
    total: float | None
    customer_name: str | None
    line_items: list[LineItem]


class RepairQuote(_Schema):
    quote_number: str | None
    quote_date: date | None
    repair_company: str | None
    total: float | None
    customer_name: str | None
    line_items: list[LineItem]


class DamagePhoto(_Schema):
    damage_description: str | None


class PoliceReport(_Schema):
    report_number: str | None
    incident_date: date | None
    customer_name: str | None
    offence: str | None


class MedicalInvoice(_Schema):
    invoice_number: str | None
    invoice_date: date | None
    provider: str | None
    patient_name: str | None
    total: float | None
    line_items: list[LineItem]


class MedicalReport(_Schema):
    patient_name: str | None
    diagnosis: str | None
    treatment_date: date | None
    provider: str | None


class Receipt(_Schema):
    merchant: str | None
    purchase_date: date | None
    item_description: str | None
    amount_paid: float | None
    line_items: list[LineItem]


class LuggageReport(_Schema):
    report_reference: str | None
    traveller_name: str | None
    incident_date: date | None
    airline: str | None


# `unknown` has no schema: nothing is read from a document of unknown type.
SCHEMAS: dict[DocumentType, type[BaseModel]] = {
    DocumentType.booking_confirmation: BookingConfirmation,
    DocumentType.cancellation_confirmation: CancellationConfirmation,
    DocumentType.medical_certificate: MedicalCertificate,
    DocumentType.repair_invoice: RepairInvoice,
    DocumentType.repair_quote: RepairQuote,
    DocumentType.damage_photo: DamagePhoto,
    DocumentType.police_report: PoliceReport,
    DocumentType.medical_invoice: MedicalInvoice,
    DocumentType.medical_report: MedicalReport,
    DocumentType.receipt: Receipt,
    DocumentType.luggage_report: LuggageReport,
}

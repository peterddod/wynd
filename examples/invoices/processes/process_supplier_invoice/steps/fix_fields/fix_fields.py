"""Wynd step fix_fields (agentic)."""
from datetime import date
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import AgenticStep


class InvoiceFields(BaseModel):
    supplier: str
    invoice_number: str
    total: float
    currency: str
    due_date: date


class FieldError(BaseModel):
    field: str
    code: str
    message: str
    fixable: bool


class FixFields(AgenticStep):
    """Correct invoice fields that failed validation by re-reading the invoice text.
    Change only the fields named in errors; copy every other field unchanged. The invoice text is the
    only source of truth. For an ambiguous currency symbol such as "$", decide the ISO 4217 code from
    the supplier's address or other currency mentions in the text. If the text does not support a
    correction, return the field unchanged; the validator will then escalate to a human.
    """

    class Input(BaseModel):
        invoice_text: str
        fields: InvoiceFields
        errors: list[FieldError]

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        supplier: str
        invoice_number: str
        total: float
        currency: str
        due_date: date

    def run(self, input: Input) -> Output: ...

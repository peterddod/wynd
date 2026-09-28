"""Wynd step validate_fields (deterministic)."""
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep

SUPPORTED_CURRENCIES = frozenset({"AUD", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP", "HKD", "INR",
                                  "JPY", "NOK", "NZD", "PLN", "SEK", "SGD", "USD", "ZAR"})
INVOICE_NUMBER = re.compile(r"[A-Z0-9][A-Z0-9/-]{1,31}")
EARLIEST_DUE = date(2000, 1, 1)
LATEST_DUE = date(2100, 12, 31)
APPROVAL_LIMIT = 1_000_000


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


class InvoiceRecord(BaseModel):
    key: str
    supplier: str
    invoice_number: str
    total: float
    currency: str
    due_date: date


class ValidateFields(DeterministicStep):
    """Validate extracted invoice fields against the accounts-payable rules and normalise them into a record."""

    class Input(BaseModel):
        fields: InvoiceFields

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        valid: bool
        fixable: bool
        errors: list[FieldError]
        fields: InvoiceFields
        record: InvoiceRecord

    def run(self, input: Input) -> Output:
        fields = self._normalise(input.fields)
        errors = self._check(fields)
        return self.Output(
            valid=not errors,
            fixable=bool(errors) and all(e.fixable for e in errors),
            errors=errors,
            fields=fields,
            record=InvoiceRecord(key=self._key(fields), **fields.model_dump()),
        )

    def _normalise(self, f: InvoiceFields) -> InvoiceFields:
        return InvoiceFields(
            supplier=" ".join(f.supplier.split()),
            invoice_number=f.invoice_number.strip().upper(),
            total=round(f.total, 2),
            currency=f.currency.strip().upper(),
            due_date=f.due_date,
        )

    def _check(self, f: InvoiceFields) -> list[FieldError]:
        errors: list[FieldError] = []
        if not f.supplier:
            errors.append(FieldError(field="supplier", code="missing_supplier",
                                     message="supplier is empty", fixable=True))
        if not INVOICE_NUMBER.fullmatch(f.invoice_number):
            errors.append(FieldError(field="invoice_number", code="bad_invoice_number",
                                     message=f"invoice_number {f.invoice_number!r} must be 2-32 characters of A-Z, 0-9, '/' or '-'",
                                     fixable=True))
        if f.total <= 0:
            errors.append(FieldError(field="total", code="non_positive_total",
                                     message=f"total {f.total:.2f} must be greater than zero", fixable=False))
        elif f.total > APPROVAL_LIMIT:
            errors.append(FieldError(field="total", code="total_over_limit",
                                     message=f"total {f.total:.2f} exceeds the 1,000,000 approval limit", fixable=False))
        if f.currency not in SUPPORTED_CURRENCIES:
            errors.append(FieldError(field="currency", code="unsupported_currency",
                                     message=f"currency {f.currency!r} is not a supported ISO 4217 code", fixable=True))
        if not EARLIEST_DUE <= f.due_date <= LATEST_DUE:
            errors.append(FieldError(field="due_date", code="due_date_out_of_range",
                                     message=f"due_date {f.due_date.isoformat()} is outside 2000-01-01..2100-12-31",
                                     fixable=True))
        return errors

    def _key(self, f: InvoiceFields) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", f.supplier.lower()).strip("-")
        return f"{slug}__{f.invoice_number}"

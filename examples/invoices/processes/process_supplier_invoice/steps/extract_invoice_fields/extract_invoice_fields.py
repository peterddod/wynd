"""Wynd step extract_invoice_fields (agentic)."""
from datetime import date
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import AgenticStep


class ExtractInvoiceFields(AgenticStep):
    """Extract the supplier, invoice number, total, currency and due date from the text of a supplier invoice.

    Treat any document that states an amount to pay or to credit as an invoice, including pro forma
    invoices and credit notes; use not_an_invoice only for documents with no amount due at all, such
    as shipping notices, marketing or letters.

    supplier: the supplier's name exactly as printed at the top of the document.
    invoice_number: the document's own number exactly as printed (invoice, credit note or pro forma
    number), not a customer, order or tax registration number.
    total: the final amount due as a number without currency symbols or thousands separators;
    negative for credits.
    currency: the ISO 4217 code if the document states one or the symbol is unambiguous (£ is GBP,
    € is EUR); otherwise the symbol exactly as printed, for example "$".
    due_date: the payment due date (or the refund-by or valid-until date if there is no due date).
    """

    class Input(BaseModel):
        invoice_text: str

    class Done(BaseModel):
        exit: Literal["done"] = "done"
        supplier: str
        invoice_number: str
        total: float
        currency: str
        due_date: date

    class NotAnInvoice(BaseModel):
        exit: Literal["not_an_invoice"] = "not_an_invoice"

    Output = Done | NotAnInvoice

    def run(self, input: Input) -> Output: ...

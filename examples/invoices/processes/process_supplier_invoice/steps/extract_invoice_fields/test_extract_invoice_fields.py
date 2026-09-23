"""Tests for extract_invoice_fields: one per proto-step example (proto/extract_invoice_fields.yaml).

Agentic: replays cassettes/ by default; `wynd test --live` records them.
"""
from pathlib import Path

from wynd.runtime.testing import expect, load_step, run_step

ExtractInvoiceFields = load_step(Path(__file__).parent)

ACME_TEXT = """\
ACME Supplies Ltd
12 Foundry Lane, Sheffield S1 2AB, United Kingdom
VAT registration: GB123456789
INVOICE
Invoice number: INV-1042
Invoice date: 1 September 2026
Due date: 1 October 2026
Bill to: Wynd Test Ltd, 1 Example Street, London EC1A 1AA
Steel brackets (box of 100)      4 x £150.00      £600.00
Galvanised bolts M8 (box of 500)      2 x £200.25      £400.50
Delivery      £200.00
Total due: £1,200.50
Payment terms: 30 days. Pay by bank transfer to sort code 00-00-00, account 12345678."""

GLOBEX_TEXT = """\
Globex Corporation
500 Market Street, Springfield, OR 97477, USA
INVOICE
Invoice No: GX-77810
Invoice date: 15 September 2026
Payment due: 15 November 2026
Bill to: Wynd Test Ltd
Consulting services, September 2026: 95 hours at USD 150.00      USD 14,250.00
Amount due (USD): 14,250.00"""

INITECH_TEXT = """\
Initech Canada Inc.
200 King Street West, Toronto, ON M5H 3T4, Canada
GST/HST registration: 123456789 RT0001
INVOICE
Invoice #: IC-5521
Issued: 20 September 2026
Due: 20 October 2026
Bill to: Wynd Test Ltd
TPS report cover sheets (1,000)      $780.00
Printer maintenance      $200.00
Total: $980.00"""

HOOLI_TEXT = """\
Hooli Ltd
1 Silicon Way, Cambridge CB1 1AA, United Kingdom
CREDIT NOTE
Credit note number: CN-311
Relates to invoice: HL-2291
Date: 18 September 2026
Refund due by: 18 October 2026
Returned: 2 x Nucleus compression licence      -£250.00
Total: -£250.00"""

SHIPPING_TEXT = """\
Globex Corporation
Shipping notification
Dear customer, your order GX-ORD-3321 has shipped.
Carrier: Example Parcel Co.
Tracking number: EP123456789GB
Expected delivery: 3 October 2026
This is not an invoice. No payment is required."""


def test_example_1(tmp_path):
    result = run_step(ExtractInvoiceFields, {"invoice_text": ACME_TEXT}, workspace=tmp_path)
    expect(
        result,
        exit="done",
        outputs={"supplier": "ACME Supplies Ltd", "invoice_number": "INV-1042", "total": 1200.5,
                 "currency": "GBP", "due_date": "2026-10-01"},
    )


def test_example_2(tmp_path):
    result = run_step(ExtractInvoiceFields, {"invoice_text": GLOBEX_TEXT}, workspace=tmp_path)
    expect(
        result,
        exit="done",
        outputs={"supplier": "Globex Corporation", "invoice_number": "GX-77810", "total": 14250.0,
                 "currency": "USD", "due_date": "2026-11-15"},
    )


def test_example_3(tmp_path):
    result = run_step(ExtractInvoiceFields, {"invoice_text": INITECH_TEXT}, workspace=tmp_path)
    expect(
        result,
        exit="done",
        outputs={"supplier": "Initech Canada Inc.", "invoice_number": "IC-5521", "total": 980.0,
                 "currency": "$", "due_date": "2026-10-20"},
    )


def test_example_4(tmp_path):
    result = run_step(ExtractInvoiceFields, {"invoice_text": HOOLI_TEXT}, workspace=tmp_path)
    expect(
        result,
        exit="done",
        outputs={"supplier": "Hooli Ltd", "invoice_number": "CN-311", "total": -250.0,
                 "currency": "GBP", "due_date": "2026-10-18"},
    )


def test_example_5(tmp_path):
    result = run_step(ExtractInvoiceFields, {"invoice_text": SHIPPING_TEXT}, workspace=tmp_path)
    expect(result, exit="not_an_invoice")

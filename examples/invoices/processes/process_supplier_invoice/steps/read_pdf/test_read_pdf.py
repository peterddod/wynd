"""Tests for read_pdf: one per proto-step example (proto/read_pdf.yaml)."""
from pathlib import Path

from wynd.runtime.testing import expect, load_step, run_step

ReadPdf = load_step(Path(__file__).parent)
BASE = Path(__file__).resolve().parents[2]   # relative example paths resolve against the process directory

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

SHIPPING_TEXT = """\
Globex Corporation
Shipping notification
Dear customer, your order GX-ORD-3321 has shipped.
Carrier: Example Parcel Co.
Tracking number: EP123456789GB
Expected delivery: 3 October 2026
This is not an invoice. No payment is required."""


def test_example_1(tmp_path):
    result = run_step(ReadPdf, {"pdf_path": str(BASE / "examples/acme_inv_1042.pdf")}, workspace=tmp_path)
    expect(result, exit="done", outputs={"pages": 1, "text": ACME_TEXT})


def test_example_2(tmp_path):
    result = run_step(ReadPdf, {"pdf_path": str(BASE / "examples/shipping_notice.pdf")}, workspace=tmp_path)
    expect(result, exit="done", outputs={"pages": 1, "text": SHIPPING_TEXT})


def test_example_3(tmp_path):
    result = run_step(ReadPdf, {"pdf_path": str(BASE / "examples/does_not_exist.pdf")}, workspace=tmp_path)
    expect(result, exit="error")

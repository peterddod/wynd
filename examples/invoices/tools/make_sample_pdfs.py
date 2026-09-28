"""Generate the sample invoice PDFs byte-for-byte deterministically (stdlib only).

Run from examples/invoices:  uv run python tools/make_sample_pdfs.py
Writes processes/process_supplier_invoice/examples/*.pdf. tools/test_make_sample_pdfs.py fails if a
committed PDF or a proto-step example text drifts from SAMPLES.
Constraints: no blank lines (pypdf drops them); characters must be cp1252 (WinAnsiEncoding).
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "processes" / "process_supplier_invoice" / "examples"

SAMPLES: dict[str, list[str]] = {
    "acme_inv_1042.pdf": [
        "ACME Supplies Ltd",
        "12 Foundry Lane, Sheffield S1 2AB, United Kingdom",
        "VAT registration: GB123456789",
        "INVOICE",
        "Invoice number: INV-1042",
        "Invoice date: 1 September 2026",
        "Due date: 1 October 2026",
        "Bill to: Wynd Test Ltd, 1 Example Street, London EC1A 1AA",
        "Steel brackets (box of 100)      4 x £150.00      £600.00",
        "Galvanised bolts M8 (box of 500)      2 x £200.25      £400.50",
        "Delivery      £200.00",
        "Total due: £1,200.50",
        "Payment terms: 30 days. Pay by bank transfer to sort code 00-00-00, account 12345678.",
    ],
    "globex_inv_77810.pdf": [
        "Globex Corporation",
        "500 Market Street, Springfield, OR 97477, USA",
        "INVOICE",
        "Invoice No: GX-77810",
        "Invoice date: 15 September 2026",
        "Payment due: 15 November 2026",
        "Bill to: Wynd Test Ltd",
        "Consulting services, September 2026: 95 hours at USD 150.00      USD 14,250.00",
        "Amount due (USD): 14,250.00",
    ],
    "initech_inv_5521.pdf": [
        "Initech Canada Inc.",
        "200 King Street West, Toronto, ON M5H 3T4, Canada",
        "GST/HST registration: 123456789 RT0001",
        "INVOICE",
        "Invoice #: IC-5521",
        "Issued: 20 September 2026",
        "Due: 20 October 2026",
        "Bill to: Wynd Test Ltd",
        "TPS report cover sheets (1,000)      $780.00",
        "Printer maintenance      $200.00",
        "Total: $980.00",
    ],
    "shipping_notice.pdf": [
        "Globex Corporation",
        "Shipping notification",
        "Dear customer, your order GX-ORD-3321 has shipped.",
        "Carrier: Example Parcel Co.",
        "Tracking number: EP123456789GB",
        "Expected delivery: 3 October 2026",
        "This is not an invoice. No payment is required.",
    ],
    "hooli_credit_note_311.pdf": [
        "Hooli Ltd",
        "1 Silicon Way, Cambridge CB1 1AA, United Kingdom",
        "CREDIT NOTE",
        "Credit note number: CN-311",
        "Relates to invoice: HL-2291",
        "Date: 18 September 2026",
        "Refund due by: 18 October 2026",
        "Returned: 2 x Nucleus compression licence      -£250.00",
        "Total: -£250.00",
    ],
    "umbrella_proforma_88.pdf": [
        "Umbrella Corporation",
        "Raccoon Business Park, Unit 4, Leeds LS1 4AP, United Kingdom",
        "PRO FORMA INVOICE",
        "Pro forma number: PF-88",
        "Date: 10 September 2026",
        "Valid until: 10 October 2026",
        "Bill to: Wynd Test Ltd",
        "Protective suits (quotation)      20 x £170.00      £3,400.00",
        "Total: £3,400.00",
        "This pro forma is not a request for payment. A final invoice will be issued on delivery.",
    ],
}


def text_of(name: str) -> str:
    """The exact text read_pdf returns for a sample."""
    return "\n".join(SAMPLES[name])


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render_pdf(lines: list[str]) -> bytes:
    for line in lines:
        if not line.strip():
            raise ValueError("blank lines are dropped by text extraction; remove them")
        line.encode("cp1252")                         # raises for characters outside WinAnsiEncoding
    ops = ["BT", "/F1 10 Tf", "13 TL", "56 780 Td", *(f"({_escape(l)}) Tj T*" for l in lines), "ET"]
    content = "\n".join(ops).encode("cp1252")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, lines in SAMPLES.items():
        (OUT / name).write_bytes(render_pdf(lines))


if __name__ == "__main__":
    main()

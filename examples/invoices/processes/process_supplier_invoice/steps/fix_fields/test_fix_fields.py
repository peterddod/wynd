"""Tests for fix_fields: one per proto-step example (proto/fix_fields.yaml).

Agentic: replays cassettes/ by default; `wynd test --live` records them.
"""
from pathlib import Path

from wynd.runtime.testing import expect, load_step, run_step

FixFields = load_step(Path(__file__).parent)

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

NORTH_WIND_TEXT = """\
North Wind Traders
Supplier ref: SUP/2026/0098
Amount payable: EUR 312.40 by 30 November 2026"""

ARCADE_TEXT = """\
Arcade Tokens Co
Invoice AT-7
Total: 50.00 credits, due 1 December 2026"""


def test_example_1(tmp_path):
    result = run_step(
        FixFields,
        {
            "invoice_text": INITECH_TEXT,
            "fields": {"supplier": "Initech Canada Inc.", "invoice_number": "IC-5521", "total": 980.0,
                       "currency": "$", "due_date": "2026-10-20"},
            "errors": [{"field": "currency", "code": "unsupported_currency",
                        "message": "currency '$' is not a supported ISO 4217 code", "fixable": True}],
        },
        workspace=tmp_path,
    )
    expect(
        result,
        exit="done",
        outputs={"supplier": "Initech Canada Inc.", "invoice_number": "IC-5521", "total": 980.0,
                 "currency": "CAD", "due_date": "2026-10-20"},
    )


def test_example_2(tmp_path):
    result = run_step(
        FixFields,
        {
            "invoice_text": NORTH_WIND_TEXT,
            "fields": {"supplier": "North Wind Traders", "invoice_number": "", "total": 312.4,
                       "currency": "EUR", "due_date": "2026-11-30"},
            "errors": [{"field": "invoice_number", "code": "bad_invoice_number",
                        "message": "invoice_number '' must be 2-32 characters of A-Z, 0-9, '/' or '-'",
                        "fixable": True}],
        },
        workspace=tmp_path,
    )
    expect(
        result,
        exit="done",
        outputs={"supplier": "North Wind Traders", "invoice_number": "SUP/2026/0098", "total": 312.4,
                 "currency": "EUR", "due_date": "2026-11-30"},
    )


def test_example_3(tmp_path):
    result = run_step(
        FixFields,
        {
            "invoice_text": ARCADE_TEXT,
            "fields": {"supplier": "Arcade Tokens Co", "invoice_number": "AT-7", "total": 50.0,
                       "currency": "CREDITS", "due_date": "2026-12-01"},
            "errors": [{"field": "currency", "code": "unsupported_currency",
                        "message": "currency 'CREDITS' is not a supported ISO 4217 code", "fixable": True}],
        },
        workspace=tmp_path,
    )
    expect(
        result,
        exit="done",
        outputs={"supplier": "Arcade Tokens Co", "invoice_number": "AT-7", "total": 50.0,
                 "currency": "CREDITS", "due_date": "2026-12-01"},
    )

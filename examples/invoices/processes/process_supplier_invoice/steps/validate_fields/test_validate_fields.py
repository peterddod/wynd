"""Tests for validate_fields: one per proto-step example (proto/validate_fields.yaml)."""
from pathlib import Path

from wynd.runtime.testing import expect, load_step, run_step

ValidateFields = load_step(Path(__file__).parent)


def test_example_1(tmp_path):
    fields = {"supplier": "ACME Supplies Ltd", "invoice_number": "INV-1042", "total": 1200.5, "currency": "GBP",
              "due_date": "2026-10-01"}
    result = run_step(ValidateFields, {"fields": fields}, workspace=tmp_path)
    expect(
        result,
        exit="done",
        outputs={
            "valid": True,
            "fixable": False,
            "errors": [],
            "fields": fields,
            "record": {"key": "acme-supplies-ltd__INV-1042", **fields},
        },
    )


def test_example_2(tmp_path):
    result = run_step(
        ValidateFields,
        {"fields": {"supplier": "  Globex   Corporation ", "invoice_number": " gx-77810", "total": 14250.004,
                    "currency": "usd", "due_date": "2026-11-15"}},
        workspace=tmp_path,
    )
    fields = {"supplier": "Globex Corporation", "invoice_number": "GX-77810", "total": 14250.0, "currency": "USD",
              "due_date": "2026-11-15"}
    expect(
        result,
        exit="done",
        outputs={
            "valid": True,
            "fixable": False,
            "errors": [],
            "fields": fields,
            "record": {"key": "globex-corporation__GX-77810", **fields},
        },
    )


def test_example_3(tmp_path):
    fields = {"supplier": "Initech Canada Inc.", "invoice_number": "IC-5521", "total": 980.0, "currency": "$",
              "due_date": "2026-10-20"}
    result = run_step(ValidateFields, {"fields": fields}, workspace=tmp_path)
    expect(
        result,
        exit="done",
        outputs={
            "valid": False,
            "fixable": True,
            "errors": [{"field": "currency", "code": "unsupported_currency",
                        "message": "currency '$' is not a supported ISO 4217 code", "fixable": True}],
            "fields": fields,
            "record": {"key": "initech-canada-inc__IC-5521", **fields},
        },
    )


def test_example_4(tmp_path):
    fields = {"supplier": "Hooli Ltd", "invoice_number": "CN-311", "total": -250.0, "currency": "GBP",
              "due_date": "2026-10-18"}
    result = run_step(ValidateFields, {"fields": fields}, workspace=tmp_path)
    expect(
        result,
        exit="done",
        outputs={
            "valid": False,
            "fixable": False,
            "errors": [{"field": "total", "code": "non_positive_total",
                        "message": "total -250.00 must be greater than zero", "fixable": False}],
            "fields": fields,
            "record": {"key": "hooli-ltd__CN-311", **fields},
        },
    )


def test_example_5(tmp_path):
    fields = {"supplier": "Hooli Ltd", "invoice_number": "CN-311", "total": -250.0, "currency": "$",
              "due_date": "2026-10-18"}
    result = run_step(ValidateFields, {"fields": fields}, workspace=tmp_path)
    expect(
        result,
        exit="done",
        outputs={
            "valid": False,
            "fixable": False,
            "errors": [
                {"field": "total", "code": "non_positive_total",
                 "message": "total -250.00 must be greater than zero", "fixable": False},
                {"field": "currency", "code": "unsupported_currency",
                 "message": "currency '$' is not a supported ISO 4217 code", "fixable": True},
            ],
            "fields": fields,
            "record": {"key": "hooli-ltd__CN-311", **fields},
        },
    )


def test_example_6(tmp_path):
    fields = {"supplier": "", "invoice_number": "", "total": 10.0, "currency": "EUR", "due_date": "2026-12-01"}
    result = run_step(ValidateFields, {"fields": fields}, workspace=tmp_path)
    expect(
        result,
        exit="done",
        outputs={
            "valid": False,
            "fixable": True,
            "errors": [
                {"field": "supplier", "code": "missing_supplier", "message": "supplier is empty", "fixable": True},
                {"field": "invoice_number", "code": "bad_invoice_number",
                 "message": "invoice_number '' must be 2-32 characters of A-Z, 0-9, '/' or '-'", "fixable": True},
            ],
            "fields": fields,
            "record": {"key": "__", **fields},
        },
    )


def test_example_7(tmp_path):
    fields = {"supplier": "ACME Supplies Ltd", "invoice_number": "INV-1042", "total": 1200.5, "currency": "GBP",
              "due_date": "1999-10-01"}
    result = run_step(ValidateFields, {"fields": fields}, workspace=tmp_path)
    expect(
        result,
        exit="done",
        outputs={
            "valid": False,
            "fixable": True,
            "errors": [{"field": "due_date", "code": "due_date_out_of_range",
                        "message": "due_date 1999-10-01 is outside 2000-01-01..2100-12-31", "fixable": True}],
            "fields": fields,
            "record": {"key": "acme-supplies-ltd__INV-1042", **fields},
        },
    )


def test_example_8(tmp_path):
    fields = {"supplier": "Globex Corporation", "invoice_number": "GX-1", "total": 2000000.0, "currency": "USD",
              "due_date": "2026-12-01"}
    result = run_step(ValidateFields, {"fields": fields}, workspace=tmp_path)
    expect(
        result,
        exit="done",
        outputs={
            "valid": False,
            "fixable": False,
            "errors": [{"field": "total", "code": "total_over_limit",
                        "message": "total 2000000.00 exceeds the 1,000,000 approval limit", "fixable": False}],
            "fields": fields,
            "record": {"key": "globex-corporation__GX-1", **fields},
        },
    )

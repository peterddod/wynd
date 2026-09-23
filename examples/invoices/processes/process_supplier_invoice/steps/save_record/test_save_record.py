"""Tests for save_record: one per proto-step example (proto/save_record.yaml), plus the written file."""
from pathlib import Path

from wynd.runtime.testing import expect, load_step, run_step, sub_tmp

SaveRecord = load_step(Path(__file__).parent)

ACME = {"key": "acme-supplies-ltd__INV-1042", "supplier": "ACME Supplies Ltd", "invoice_number": "INV-1042",
        "total": 1200.5, "currency": "GBP", "due_date": "2026-10-01"}
NORTH_WIND = {"key": "north-wind-traders__SUP/2026/0098", "supplier": "North Wind Traders",
              "invoice_number": "SUP/2026/0098", "total": 312.4, "currency": "EUR", "due_date": "2026-11-30"}


def test_example_1(tmp_path):
    inputs = sub_tmp({"record": ACME, "dest": "{tmp}/records"}, tmp_path)
    result = run_step(SaveRecord, inputs, workspace=tmp_path / "ws")
    expect(
        result,
        exit="done",
        outputs=sub_tmp({"record": ACME, "path": "{tmp}/records/acme-supplies-ltd__INV-1042.json"}, tmp_path),
    )


def test_example_2(tmp_path):
    inputs = sub_tmp({"record": NORTH_WIND, "dest": "{tmp}/nested/records"}, tmp_path)
    result = run_step(SaveRecord, inputs, workspace=tmp_path / "ws")
    expect(
        result,
        exit="done",
        outputs=sub_tmp(
            {"record": NORTH_WIND, "path": "{tmp}/nested/records/north-wind-traders__SUP_2026_0098.json"}, tmp_path
        ),
    )


def test_record_file_contents(tmp_path):
    """The file holds the record as 2-space JSON with a trailing newline and replaces an existing one."""
    dest = tmp_path / "records"
    dest.mkdir()
    (dest / "acme-supplies-ltd__INV-1042.json").write_text("stale\n", encoding="utf-8")
    result = run_step(SaveRecord, {"record": ACME, "dest": str(dest)}, workspace=tmp_path / "ws")
    expect(result, exit="done")
    assert (dest / "acme-supplies-ltd__INV-1042.json").read_text(encoding="utf-8") == (
        "{\n"
        '  "key": "acme-supplies-ltd__INV-1042",\n'
        '  "supplier": "ACME Supplies Ltd",\n'
        '  "invoice_number": "INV-1042",\n'
        '  "total": 1200.5,\n'
        '  "currency": "GBP",\n'
        '  "due_date": "2026-10-01"\n'
        "}\n"
    )
    assert sorted(p.name for p in dest.iterdir()) == ["acme-supplies-ltd__INV-1042.json"]

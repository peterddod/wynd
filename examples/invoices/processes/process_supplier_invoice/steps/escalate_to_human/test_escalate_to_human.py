"""Tests for escalate_to_human: one per proto-step example (proto/escalate_to_human.yaml), plus the ticket JSON."""
from pathlib import Path

from wynd.runtime.testing import expect, load_step, run_step, sub_tmp

EscalateToHuman = load_step(Path(__file__).parent)

HOOLI = {"supplier": "Hooli Ltd", "invoice_number": "CN-311", "total": -250.0, "currency": "GBP",
         "due_date": "2026-10-18"}
HOOLI_ERRORS = [{"field": "total", "code": "non_positive_total", "message": "total -250.00 must be greater than zero",
                 "fixable": False}]
UMBRELLA = {"supplier": "Umbrella Corporation", "invoice_number": "PF-88", "total": 3400.0, "currency": "GBP",
            "due_date": "2026-10-10"}


def test_example_1(tmp_path):
    inputs = sub_tmp(
        {"fields": HOOLI, "errors": HOOLI_ERRORS, "queue_dir": "{tmp}/escalations", "run_id": "run-test-1"}, tmp_path
    )
    result = run_step(EscalateToHuman, inputs, workspace=tmp_path / "ws")
    expect(result, exit="done", outputs=sub_tmp({"ticket_path": "{tmp}/escalations/run-test-1.json"}, tmp_path))


def test_example_2(tmp_path):
    inputs = sub_tmp({"fields": UMBRELLA, "errors": [], "queue_dir": "{tmp}/queue", "run_id": "run-test-2"}, tmp_path)
    result = run_step(EscalateToHuman, inputs, workspace=tmp_path / "ws")
    expect(result, exit="done", outputs=sub_tmp({"ticket_path": "{tmp}/queue/run-test-2.json"}, tmp_path))


def test_ticket_contents(tmp_path):
    """Sorted keys, 2-space indent, trailing newline; the reasons are the error messages, or the generic reason."""
    queue = tmp_path / "queue"
    run_step(EscalateToHuman, {"fields": HOOLI, "errors": HOOLI_ERRORS, "queue_dir": str(queue),
                               "run_id": "run-example-5"}, workspace=tmp_path / "ws1")
    run_step(EscalateToHuman, {"fields": UMBRELLA, "errors": [], "queue_dir": str(queue),
                               "run_id": "run/with:odd chars"}, workspace=tmp_path / "ws2")
    assert sorted(p.name for p in queue.iterdir()) == ["run-example-5.json", "run_with_odd_chars.json"]
    assert (queue / "run-example-5.json").read_text(encoding="utf-8") == (
        "{\n"
        '  "errors": [\n'
        "    {\n"
        '      "code": "non_positive_total",\n'
        '      "field": "total",\n'
        '      "fixable": false,\n'
        '      "message": "total -250.00 must be greater than zero"\n'
        "    }\n"
        "  ],\n"
        '  "fields": {\n'
        '    "currency": "GBP",\n'
        '    "due_date": "2026-10-18",\n'
        '    "invoice_number": "CN-311",\n'
        '    "supplier": "Hooli Ltd",\n'
        '    "total": -250.0\n'
        "  },\n"
        '  "reasons": [\n'
        '    "total -250.00 must be greater than zero"\n'
        "  ],\n"
        '  "run_id": "run-example-5"\n'
        "}\n"
    )
    assert (queue / "run_with_odd_chars.json").read_text(encoding="utf-8") == (
        "{\n"
        '  "errors": [],\n'
        '  "fields": {\n'
        '    "currency": "GBP",\n'
        '    "due_date": "2026-10-10",\n'
        '    "invoice_number": "PF-88",\n'
        '    "supplier": "Umbrella Corporation",\n'
        '    "total": 3400.0\n'
        "  },\n"
        '  "reasons": [\n'
        '    "routed to review by the process; see `wynd trace run/with:odd chars`"\n'
        "  ],\n"
        '  "run_id": "run/with:odd chars"\n'
        "}\n"
    )

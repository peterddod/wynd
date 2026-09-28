"""Process search (PLAN §8.1; `$DRAFTS/06 §5.6`): the pure `search` function and `ProcessService.list` over it."""

from __future__ import annotations

import pytest

from wynd.controller.errors import Invalid
from wynd.controller.models import ProcessStatus
from wynd.controller.search import search


def doc(pid: str, *, name: str | None = None, goal: str | None = None, steps=()) -> dict:
    return {"id": pid, "name": name or pid.rpartition("/")[2], "goal": goal, "steps": list(steps)}


def status(**flags: bool) -> ProcessStatus:
    values = {"design": False, "compiled": False, "built": False, "released": False, **flags}
    return ProcessStatus(**values, tests="unknown")


def ids(hits) -> list[tuple[str, int]]:
    return [(pid, score) for pid, score, _ in hits]


DOCS = [
    doc("finance/refunds", goal="Refund a customer order."),
    doc("finance/invoices", goal="Turn a supplier invoice PDF into a record.",
        steps=[("extract", "Given the invoice text, extract the fields."), ("save", "Write the record.")]),
    doc("ops/notify", goal="Send a message.", steps=[("send_invoice", "Email the document.")]),
    doc("ops/archive", goal=None, steps=[("store", "Archive an Invoice PDF.")]),
]


def test_an_empty_query_matches_everything_sorted_by_id():
    assert ids(search(DOCS, {}, "", [])) == [
        ("finance/invoices", 0), ("finance/refunds", 0), ("ops/archive", 0), ("ops/notify", 0)]
    assert all(matches == [] for _, _, matches in search(DOCS, {}, "  ", []))


def test_score_is_the_best_weight_per_token_and_orders_hits():
    # invoices: id (3); archive: instruction (1); notify: step name (1)
    assert ids(search(DOCS, {}, "INVOICE", [])) == [("finance/invoices", 3), ("ops/archive", 1), ("ops/notify", 1)]
    assert ids(search(DOCS, {}, "pdf", [])) == [("finance/invoices", 2), ("ops/archive", 1)]   # goal, instruction
    assert ids(search(DOCS, {}, "invoice record", [])) == [("finance/invoices", 5)]        # 3 + 2
    assert ids(search(DOCS, {}, "finance", [])) == [("finance/invoices", 3), ("finance/refunds", 3)]


def test_every_token_must_match_somewhere():
    assert ids(search(DOCS, {}, "invoice refund", [])) == []
    assert ids(search(DOCS, {}, "archive pdf", [])) == [("ops/archive", 4)]                # id + instruction
    assert search(DOCS, {}, "nothing-like-this", []) == []


def test_matches_carry_fields_steps_and_snippets():
    goal = "Collect every " + "very " * 12 + "long supplier invoice and then " + "file " * 12 + "it."
    hits = search([doc("p", goal=goal, steps=[("extract", "Read the invoice."), ("invoice_mail", "")])], {},
                  "invoice", [])
    (_, score, matches), = hits
    assert score == 2
    assert [(m.field, m.step) for m in matches] == [("goal", None), ("instruction", "extract"),
                                                     ("instruction", "invoice_mail")]
    snippet = matches[0].snippet
    assert snippet.startswith("…") and snippet.endswith("…")
    assert snippet == "…" + goal[goal.index("invoice") - 40:goal.index("invoice") + len("invoice") + 40] + "…"
    assert matches[1].snippet == "Read the invoice." and matches[2].snippet == "invoice_mail"


def test_at_most_three_matches_and_whitespace_is_collapsed():
    steps = [(f"s{i}", f"Handle the\n  invoice   part {i}.") for i in range(5)]
    (_, score, matches), = search([doc("invoices", goal="An invoice.", steps=steps)], {}, "invoice", [])
    assert score == 3 and len(matches) == 3
    assert [(m.field, m.step, m.snippet) for m in matches] == [
        ("id", None, "invoices"), ("name", None, "invoices"), ("goal", None, "An invoice.")]
    (_, _, matches), = search([doc("p", steps=steps)], {}, "part", [])
    assert [m.snippet for m in matches] == ["Handle the invoice part 0.", "Handle the invoice part 1.",
                                            "Handle the invoice part 2."]


def test_flags_are_anded_against_the_derived_status():
    statuses = {
        "finance/invoices": status(compiled=True, built=True, released=True),
        "finance/refunds": status(compiled=True, built=True),
        "ops/notify": status(design=True),
    }                                                                           # ops/archive has no status
    assert ids(search(DOCS, statuses, "", ["built"])) == [("finance/invoices", 0), ("finance/refunds", 0)]
    assert ids(search(DOCS, statuses, "", ["built", "released"])) == [("finance/invoices", 0)]
    assert ids(search(DOCS, statuses, "invoice", ["design"])) == [("ops/notify", 1)]
    assert ids(search(DOCS, statuses, "", ["design", "built"])) == []
    with pytest.raises(Invalid, match="unknown status flag 'shipped'"):
        search(DOCS, statuses, "", ["shipped"])


def test_limit():
    assert ids(search(DOCS, {}, "", [], limit=2)) == [("finance/invoices", 0), ("finance/refunds", 0)]


def test_process_list_searches_the_workspace(controller):
    (p1,) = controller.processes.list(q="upper")
    assert p1.id == "p1" and p1.status is not None
    assert [(m.field, m.step) for m in p1.matches] == [("goal", None), ("instruction", "upper")]
    assert p1.matches[1].snippet == "Upper-case the text; blank text takes the emp…"      # 40 chars after "upper"
    assert [s.id for s in controller.processes.list(q="p", flags=["design"])] == ["p2"]
    assert [s.id for s in controller.processes.list(q="name tag")] == ["p2"]

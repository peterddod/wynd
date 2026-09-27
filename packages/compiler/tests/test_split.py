import re
import shutil
import subprocess
from pathlib import Path

import pytest

from wynd.compiler.split import (
    SplitPlan,
    apply_split,
    has_split_markers,
    remove_split,
    rename_step_refs,
)
from wynd.spec.process_doc import ProcessDoc
from wynd.spec.yamlio import parse_model

REPO = Path(__file__).resolve().parents[3]
DOGFOOD = REPO / "examples" / "invoices"
PID = "process_supplier_invoice"
PLAN = SplitPlan(node="extract", agentic_node="extract_agentic",
                 agentic_use="./steps/extract_invoice_fields_agentic", input_fields=["invoice_text"])

EXPECTED_TAIL = """\
  - from: escalate.done
    to: $exit.needs_review
  # wynd:split extract begin (added by wynd compile; recompiling extract_invoice_fields may remove it)
  - from: extract.error
    to: extract_agentic
    with: {invoice_text: steps.extract.outputs.inputs.invoice_text}
  - from: extract_agentic.done
    to: validate
    with: {fields: steps.extract_agentic.outputs}
  - {from: extract_agentic.not_an_invoice, to: $exit.not_an_invoice}
  # wynd:split extract end
"""

EXPECTED_STEPS = """\
  escalate: { use: ./steps/escalate_to_human }
  # wynd:split extract begin (added by wynd compile; recompiling extract_invoice_fields may remove it)
  extract_agentic: {use: ./steps/extract_invoice_fields_agentic}
  # wynd:split extract end
edges:
"""


def _dogfood_text() -> str:
    return (DOGFOOD / "processes" / PID / "process.yaml").read_text()


def _doc(text: str) -> ProcessDoc:
    return parse_model(text, ProcessDoc)


def test_apply_split_on_dogfood_process():
    text = _dogfood_text()
    out = apply_split(text, _doc(text), PLAN)
    assert out.startswith(text[: text.index("  escalate: { use:")])
    assert EXPECTED_STEPS in out
    assert out.endswith(EXPECTED_TAIL)
    assert "# Branches evaluated top to bottom" in out          # comments survive
    doc = _doc(out)
    assert doc.steps["extract_agentic"].use == "./steps/extract_invoice_fields_agentic"
    handoff = doc.edge("extract.error")
    assert handoff.to[0].step == "extract_agentic"
    assert handoff.to[0].with_ == {"invoice_text": "steps.extract.outputs.inputs.invoice_text"}
    assert doc.edge("extract_agentic.done").to[0].with_ == {"fields": "steps.extract_agentic.outputs"}
    assert doc.edge("extract_agentic.not_an_invoice").to[0].step == "$exit.not_an_invoice"
    assert has_split_markers(out, "extract")


def test_apply_split_twice_replaces_the_earlier_split():
    text = _dogfood_text()
    once = apply_split(text, _doc(text), PLAN)
    assert apply_split(once, _doc(once), PLAN) == once


def test_remove_split_with_markers_restores_the_original():
    text = _dogfood_text()
    out = apply_split(text, _doc(text), PLAN)
    assert remove_split(out, _doc(out), "extract") == text


def test_remove_split_without_markers_is_structural():
    text = _dogfood_text()
    out = re.sub(r"^.*wynd:split.*\n", "", apply_split(text, _doc(text), PLAN), flags=re.M)
    assert not has_split_markers(out, "extract")
    removed = remove_split(out, _doc(out), "extract")
    doc, original = _doc(removed), _doc(text)
    assert doc.steps.keys() == original.steps.keys()
    assert [e.model_dump(by_alias=True) for e in doc.edges] == [e.model_dump(by_alias=True) for e in original.edges]


def test_remove_split_without_a_split_is_identity():
    text = _dogfood_text()
    assert remove_split(text, _doc(text), "extract") == text


def test_apply_split_refuses_a_taken_name():
    text = _dogfood_text().replace("  escalate: { use: ./steps/escalate_to_human }\n",
                                   "  escalate: { use: ./steps/escalate_to_human }\n"
                                   "  extract_agentic: { use: ./steps/escalate_to_human }\n")
    with pytest.raises(ValueError, match="already taken"):
        apply_split(text, _doc(text), PLAN)


def test_split_copies_conditions_and_limits_with_renamed_refs():
    text = """\
kind: process
name: p
entry: a
inputs: {x: string}
outputs: {y: string}
steps:
  a: { use: ./steps/a }
  b: { use: ./steps/b }
edges:
  - from: a.done
    to:
      - step: b
        name: again
        when: steps.a.outputs.n > 1 and edges["a.done"].again.taken < 2
        limits: { max_traversals: 3 }
        with: { v: steps.a.outputs.y, label: '"steps.a"' }
      - step: $exit.done
        with: { y: steps.a.outputs.y }
"""
    plan = SplitPlan(node="a", agentic_node="a_agentic", agentic_use="./steps/a_agentic", input_fields=["x"])
    doc = _doc(apply_split(text, _doc(text), plan))
    copied = doc.edge("a_agentic.done")
    assert copied.to[0].when == 'steps.a_agentic.outputs.n > 1 and edges["a_agentic.done"].again.taken < 2'
    assert copied.to[0].name == "again"
    assert copied.to[0].limits.max_traversals == 3
    assert copied.to[0].with_ == {"v": "steps.a_agentic.outputs.y", "label": '"steps.a"'}
    assert copied.to[1].with_ == {"y": "steps.a_agentic.outputs.y"}
    assert doc.edge("a.done") == _doc(text).edge("a.done")


@pytest.mark.parametrize(("expr", "expected"), [
    ("steps.extract.outputs.total > 1", "steps.extract_agentic.outputs.total > 1"),
    ("steps.extractor.outputs.total", "steps.extractor.outputs.total"),
    ('edges["extract.done"][0].taken', 'edges["extract_agentic.done"][0].taken'),
    ("edges['extract.done'].retry.taken", "edges['extract_agentic.done'].retry.taken"),
    ('edges["extractor.done"][0].taken', 'edges["extractor.done"][0].taken'),
    ('"steps.extract.outputs"', '"steps.extract.outputs"'),
    ('steps["extract"].exit == "done"', 'steps["extract_agentic"].exit == "done"'),
    ("coalesce(steps.extract.outputs.a, (steps.extract).exit)",
     "coalesce(steps.extract_agentic.outputs.a, (steps.extract_agentic).exit)"),
    ("previous.outputs.total + steps.fix.runs", "previous.outputs.total + steps.fix.runs"),
    ("steps.a.outputs[steps.extract.outputs.key]", "steps.a.outputs[steps.extract_agentic.outputs.key]"),
    ("1 +\n  steps.extract.runs", "1 +\n  steps.extract_agentic.runs"),
])
def test_rename_step_refs(expr, expected):
    assert rename_step_refs(expr, "extract", "extract_agentic") == expected


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_split_dogfood_passes_process_validation(tmp_path):
    from wynd.process import load_workspace, validate_process

    ws = tmp_path / "ws"
    shutil.copytree(DOGFOOD, ws, ignore=shutil.ignore_patterns("__pycache__", ".wynd"))
    _git(ws, "init", "-q")
    pdir = ws / "processes" / PID
    agentic = pdir / "steps" / "extract_invoice_fields_agentic"
    shutil.copytree(pdir / "steps" / "extract_invoice_fields", agentic, ignore=shutil.ignore_patterns("cassettes"))
    (agentic / "extract_invoice_fields.py").rename(agentic / "extract_invoice_fields_agentic.py")
    (agentic / "test_extract_invoice_fields.py").unlink()
    lock = (agentic / "step.lock.yaml").read_text()
    lock = lock.replace("name: extract_invoice_fields\n", "name: extract_invoice_fields_agentic\n")
    lock = lock.replace("entrypoint: extract_invoice_fields:", "entrypoint: extract_invoice_fields_agentic:")
    (agentic / "step.lock.yaml").write_text(lock)
    text = (pdir / "process.yaml").read_text()
    (pdir / "process.yaml").write_text(apply_split(text, _doc(text), PLAN))

    report = validate_process(load_workspace(ws), PID)
    errors = [d.format() for d in report.diagnostics if d.severity == "error"]
    assert errors == []
    assert "extract_agentic" in report.normalized[PID].steps

"""The dogfood (`examples/invoices`) validates with exactly two `I201` infos and nothing else (PLAN §6.3, §14
PROC-VAL Accept), with its compiled step packages and in the design phase (protos only)."""

import pytest

from wynd.process.validation import validate_process
from wynd.process.workspace import load_workspace
from wynd.spec.base import DEFAULT_MAX_TRAVERSALS
from wynd.spec.errors import format_loc

PID = "process_supplier_invoice"
STEPS = f"processes/{PID}/steps"


@pytest.fixture(params=["as_is", "design_phase"])
def dogfood(request, make_repo, repo_root):
    files = {STEPS: None} if request.param == "design_phase" else None
    return make_repo(source=repo_root / "examples/invoices", files=files)


def test_dogfood_validates_with_exactly_two_i201(dogfood):
    report = validate_process(load_workspace(dogfood), PID)
    assert report.ok
    assert [(d.code, d.severity, format_loc(d.loc)) for d in report.diagnostics] == [
        ("I201", "info", "edges[3].to[1]"),
        ("I201", "info", "edges[4].to[0]"),
    ]
    assert [d.message for d in report.diagnostics] == [
        "branch 'validate.done[1]' → 'fix' lies on a cycle; max_traversals defaulted to 10",
        "branch 'fix.done[0]' → 'validate' lies on a cycle; max_traversals defaulted to 10",
    ]


def test_dogfood_normalized_and_env_refs(dogfood):
    report = validate_process(load_workspace(dogfood), PID)
    assert list(report.normalized) == [PID]
    filled = {
        (edge.from_, j): branch.limits.max_traversals
        for edge in report.normalized[PID].edges
        for j, branch in enumerate(edge.to)
        if branch.limits is not None
    }
    assert filled == {("validate.done", 1): DEFAULT_MAX_TRAVERSALS, ("fix.done", 0): DEFAULT_MAX_TRAVERSALS}
    assert "max_traversals" not in (dogfood / "processes" / PID / "process.yaml").read_text()
    assert report.env_refs == {
        "REVIEW_DIR": [f"edge:{PID}:validate.done[0].dest"],
        "RECORDS_DIR": [f"edge:{PID}:validate.done[0].dest"],
        "ESCALATIONS_DIR": [f"edge:{PID}:validate.done[2].queue_dir"],
    }

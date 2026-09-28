"""`validate` / `validate_process`: the report, the closure, the hooks and the tree the file rules read (PLAN §6.2,
§6.3)."""

import pytest

import wynd.process.latency as latency_module
import wynd.process.validation.agentic as agentic_module
import wynd.process.validation.typecheck as typecheck_module
from wynd.process.errors import ProcessNotFound
from wynd.process.validation import ValidationReport, format_diagnostic, validate, validate_process
from wynd.process.workspace import load_workspace
from wynd.runtime.providers import ProviderInfo
from wynd.spec.errors import Diagnostic, format_loc
from wynd.spec.fragments import EnvFragment
from wynd.spec.lockfiles import EdgesLock

KINDS = {"claude-code": "agent", "anthropic": "model"}


def catalog(name: str) -> ProviderInfo:
    return ProviderInfo(name, KINDS[name], {}, EnvFragment())


PROTO = """kind: proto_step
name: {name}
instruction: Do {name}.
inputs:
  value: string
outputs:
  value: string
examples:
  - inputs: {{ value: a }}
    outputs: {{ value: a }}
"""

PARENT = """kind: process
name: parent
entry: a
inputs:
  value: string
outputs:
  value: string
steps:
  a:     { use: ./steps/a }
  child: { use: process:child }
edges:
  - from: a.done
    to:
      - step: child
        with: { value: steps.a.outputs.value }
      - step: $exit.done
        with: { value: steps.a.outputs.value }
  - from: child.done
    to: $exit.done
    with: { value: steps.child.outputs.value }
"""

CHILD = """kind: process
name: child
entry: b
inputs:
  value: string
outputs:
  value: string
steps:
  b: { use: ./steps/b }
edges:
  - from: b.done
    to:
      - step: b
        when: steps.b.runs < 2
        with: { value: steps.b.outputs.value }
      - step: $exit.done
        with: { value: steps.b.outputs.valu }
"""


@pytest.fixture
def family(make_repo):
    return make_repo(files={
        "wynd.yaml": "process_roots: [processes]\n",
        "processes/parent/process.yaml": PARENT,
        "processes/parent/proto/a.yaml": PROTO.format(name="a"),
        "processes/child/process.yaml": CHILD,
        "processes/child/proto/b.yaml": PROTO.format(name="b"),
    })


def test_report_covers_the_closure(family):
    report = validate_process(load_workspace(family), "parent", providers=catalog)
    assert isinstance(report, ValidationReport) and report.process == "parent"
    assert list(report.normalized) == ["parent", "child"]
    assert [(d.process, d.code, format_loc(d.loc)) for d in report.diagnostics] == [
        ("child", "I201", "edges[0].to[0]"),
        ("child", "E-REF-FIELD", "edges[0].to[1].with.value"),
        ("parent", "W-BRANCH-UNREACHABLE", "edges[0].to[1]"),
    ]
    assert not report.ok
    # the branch after the else is dropped from the normalised parent only
    assert [b.step for b in report.normalized["parent"].edges[0].to] == ["child"]
    child_edge = report.normalized["child"].edges[0]
    assert [b.limits.max_traversals if b.limits else None for b in child_edge.to] == [10, None]


def test_diagnostics_are_sorted(family):
    diagnostics = validate_process(load_workspace(family), "parent", providers=catalog).diagnostics
    keys = [(d.process or "", d.file or "", d.line or 0, d.code) for d in diagnostics]
    assert keys == sorted(keys)


def test_validate_process_unknown_id(family):
    with pytest.raises(ProcessNotFound):
        validate_process(load_workspace(family), "nope")


def test_validate_process_rejects_unknown_keywords(family):
    with pytest.raises(TypeError):
        validate_process(load_workspace(family), "parent", colour="red")


def test_validate_reads_files_through_the_loader_tree(make_repo):
    """`validate(lp)` runs the file rules through the tree the loader attached (`lp._tree`); without one it skips them
    and matches `validate_process` otherwise."""
    from pathlib import Path

    fixtures = Path(__file__).parent / "fixtures"
    case = fixtures / "cases" / "lint"
    files = {str(p.relative_to(case)): p.read_bytes() for p in case.rglob("*") if p.is_file()
             and p.name != "expected.json"}
    ws = load_workspace(make_repo(source=fixtures / "base", files=files))
    full = validate_process(ws, "main", providers=catalog)
    lp = ws.load_process("main")
    lint = [d for d in full.diagnostics if d.code.startswith("L")]
    assert lint

    lp.__dict__.pop("_tree", None)
    without = validate(lp, providers=catalog)
    assert without.diagnostics == [d for d in full.diagnostics if not d.code.startswith("L")]

    lp._tree = ws.tree
    assert validate(lp, providers=catalog).diagnostics == full.diagnostics


def test_hooks_receive_sites_edges_lock_and_stats(family, monkeypatch, write_files):
    seen = {}

    def check_types(lp, sites):
        seen.setdefault("sites", {})[lp.id] = list(sites)
        return [Diagnostic("warning", "W-TYPE-ASSIGN", "typed", process=lp.id)]

    def check_agentic_edges(lp, edges_lock):
        seen.setdefault("locks", {})[lp.id] = edges_lock
        return []

    def latency_warnings(lp, stats):
        seen["stats"] = (lp.id, stats)
        return []

    monkeypatch.setattr(typecheck_module, "check_types", check_types)
    monkeypatch.setattr(agentic_module, "check_agentic_edges", check_agentic_edges)
    monkeypatch.setattr(latency_module, "latency_warnings", latency_warnings)
    write_files(family, {"processes/parent/edges.lock.yaml": "wynd: 1\nedges: {}\n"})

    report = validate_process(load_workspace(family), "parent", providers=catalog, stats="STATS")
    assert [d.process for d in report.diagnostics if d.code == "W-TYPE-ASSIGN"] == ["child", "parent"]
    targets = {site.target for site in seen["sites"]["parent"]}
    assert {"with", "exit", "entry"} <= targets
    assert set(seen["locks"]) == {"parent", "child"}
    assert all(isinstance(lock, EdgesLock) for lock in seen["locks"].values())
    assert seen["stats"] == ("parent", "STATS")


def test_invalid_edges_lock_is_reported(family, write_files):
    write_files(family, {"processes/parent/edges.lock.yaml": "wynd: 1\nedges: {x: {tier: huge}}\n"})
    report = validate_process(load_workspace(family), "parent", providers=catalog)
    assert any(d.file == "processes/parent/edges.lock.yaml" and d.severity == "error" for d in report.diagnostics)


def test_format_diagnostic():
    located = Diagnostic("error", "E204", "bad", file="p/process.yaml", line=3, column=5, loc=("edges", 0, "from"),
                         process="p")
    assert format_diagnostic(located) == located.format()
    assert format_diagnostic(Diagnostic("warning", "W206", "slow", process="p")) == "[p] warning[W206]: slow"

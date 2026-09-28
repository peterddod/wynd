"""M5 agentic-edge validation: one positive and one negative case per code (PLAN §3.2, §6.3 pass 7; `$DRAFTS/08
§4.3`, §4.8), locations, and the hook as `validate_process` runs it on a real workspace."""

import ast
import re
from pathlib import Path

import pytest

import wynd.process.validation.agentic as agentic_module
from wynd.process.edges_lock import sync_edge_lock
from wynd.process.loader import LoadedProcess
from wynd.process.validation import validate_process
from wynd.process.validation.agentic import CODES, check_agentic_edges
from wynd.process.workspace import load_workspace
from wynd.spec.errors import format_loc
from wynd.spec.lockfiles import EdgesLock, check_hash, dump_lock
from wynd.spec.process_doc import ProcessDoc
from wynd.spec.yamlio import parse_model

PID = "main"
PATH = "processes/main/process.yaml"
LOCK_PATH = "processes/main/edges.lock.yaml"
PLAN_CODES = {
    "E-CHECK-NOT-AGENTIC", "E-AGENTIC-NO-CHECK", "E-CHECK-EMPTY", "E-CHECK-CONTEXT", "W-AGENTIC-NO-ELSE",
    "W-AGENTIC-EDGE-FAST", "W-EDGE-LOCK-MISSING", "W-EDGE-LOCK-STALE", "W-EDGE-LOCK-ORPHAN", "W-EDGE-PROVIDER-UNKNOWN",
}

CHECK = "The document is a final supplier invoice."
CONTEXT = ["steps.read.outputs"]

PROCESS = """kind: process
name: main
goal: File supplier documents.
{latency}entry: read
inputs:
  value: string
outputs:
  done:
    value: string
  rejected:
    value: string
steps:
  read:     {{ use: ./steps/read }}
  validate: {{ use: ./steps/validate }}
  fix:      {{ use: ./steps/fix }}
  save:     {{ use: ./steps/save }}
edges:
  - from: read.done
    to: validate
    with: {{ value: steps.read.outputs.value }}
{edge}  - from: fix.done
    to: validate
    with: {{ value: steps.fix.outputs.value }}
  - from: save.done
    to: $exit.done
    with: {{ value: steps.save.outputs.value }}
"""

AGENTIC = """  - from: validate.done
    kind: agentic
    to:
      - step: save
        name: save
        check: The document is a final supplier invoice.
        context: [steps.read.outputs]
        with: { value: steps.validate.outputs.value }
      - step: fix
        when: steps.fix.runs < 2
        with: { value: steps.validate.outputs.value }
      - step: $exit.rejected
        with: { value: steps.validate.outputs.value }
"""

LOCKED = f"wynd: 1\nedges:\n  validate.done[save]:\n    check_hash: '{check_hash(CHECK, CONTEXT)}'\n"

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


def process(edge: str = AGENTIC, latency: str | None = None) -> str:
    return PROCESS.format(edge=edge, latency=f"latency: {latency}\n" if latency else "")


def loaded(text: str) -> LoadedProcess:
    doc = parse_model(text, ProcessDoc, PATH)
    return LoadedProcess(id=PID, dir="processes/main", path=PATH, doc=doc, source=doc._source, steps={}, children={},
                         diagnostics=[])


def edges_lock(text: str | None) -> EdgesLock:
    return EdgesLock() if text is None else parse_model(text, EdgesLock, LOCK_PATH)


def findings(text: str, lock: str | None = LOCKED) -> list[tuple[str, str]]:
    return [(d.code, format_loc(d.loc)) for d in check_agentic_edges(loaded(text), edges_lock(lock))]


def only(text: str, code: str, lock: str | None = LOCKED) -> list[str]:
    return [loc for found, loc in findings(text, lock) if found == code]


def line_of(text: str, needle: str) -> int:
    return next(n for n, line in enumerate(text.splitlines(), 1) if needle in line)


def entry_line(lock: str, key: str) -> int:
    """Source marks are value nodes: a lock entry's mapping starts on the line after its key."""
    return line_of(lock, f"{key}:") + 1


def test_a_locked_agentic_edge_with_an_else_is_clean():
    assert findings(process()) == []


def test_codes_table_is_the_plan_namespace():
    assert set(CODES) == PLAN_CODES
    assert all(severity == ("error" if code.startswith("E-") else "warning") for code, (severity, _) in CODES.items())
    tree = ast.parse(Path(agentic_module.__file__).read_text())
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and re.match(r"^[EW]-[A-Z][A-Z-]*$", n.value)}
    assert literals == PLAN_CODES


# --- E-CHECK-NOT-AGENTIC ---------------------------------------------------------------------------------------------

def test_check_on_a_deterministic_edge_is_an_error():
    text = process(AGENTIC.replace("    kind: agentic\n", ""))
    diagnostics = check_agentic_edges(loaded(text), EdgesLock())
    assert [(d.code, d.severity, format_loc(d.loc)) for d in diagnostics] == [
        ("E-CHECK-NOT-AGENTIC", "error", "edges[1].to[0].check"),
    ]
    assert diagnostics[0].message == (
        "branch 'validate.done[save]' has check: and context: but edge 'validate.done' is not kind: agentic"
    )
    assert (diagnostics[0].file, diagnostics[0].line) == (PATH, line_of(text, "check: The document"))


def test_context_alone_on_a_deterministic_edge_is_an_error():
    edge = AGENTIC.replace("    kind: agentic\n", "").replace(f"        check: {CHECK}\n", "")
    assert findings(process(edge), None) == [("E-CHECK-NOT-AGENTIC", "edges[1].to[0].context")]


def test_check_on_an_agentic_edge_is_not_flagged():
    assert only(process(), "E-CHECK-NOT-AGENTIC") == []


# --- E-AGENTIC-NO-CHECK ----------------------------------------------------------------------------------------------

def test_agentic_edge_without_any_check_is_an_error():
    edge = AGENTIC.replace(f"        check: {CHECK}\n", "").replace("        context: [steps.read.outputs]\n",
                                                                    "        when: steps.validate.runs > 0\n")
    assert findings(process(edge), None) == [("E-AGENTIC-NO-CHECK", "edges[1].kind")]


def test_agentic_edge_with_a_check_has_one():
    assert only(process(), "E-AGENTIC-NO-CHECK") == []


# --- E-CHECK-EMPTY ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("check, problem", [
    ("'   '", "blank"),
    ("x" * 2001, "2001 characters long (at most 2000)"),
])
def test_blank_or_overlong_check_is_an_error(check, problem):
    text = process(AGENTIC.replace(CHECK, check))
    [found] = [d for d in check_agentic_edges(loaded(text), EdgesLock()) if d.code == "E-CHECK-EMPTY"]
    assert format_loc(found.loc) == "edges[1].to[0].check"
    assert found.message == f"check of branch 'validate.done[save]' is {problem}"


def test_check_of_exactly_2000_characters_is_accepted():
    assert only(process(AGENTIC.replace(CHECK, "x" * 2000)), "E-CHECK-EMPTY") == []


# --- E-CHECK-CONTEXT -------------------------------------------------------------------------------------------------

def with_context(entries: str, when: str | None = None) -> str:
    edge = AGENTIC.replace("context: [steps.read.outputs]", f"context: [{entries}]")
    if when is not None:
        edge = edge.replace("        name: save\n", f"        name: save\n        when: {when}\n")
    return process(edge)


def test_context_entries_that_do_not_parse_or_name_unknown_steps_are_errors():
    text = with_context("bogus, steps.read.text, steps.nope.outputs")
    diagnostics = [d for d in check_agentic_edges(loaded(text), EdgesLock()) if d.code == "E-CHECK-CONTEXT"]
    assert [format_loc(d.loc) for d in diagnostics] == [
        "edges[1].to[0].context[0]", "edges[1].to[0].context[1]", "edges[1].to[0].context[2]",
    ]
    assert diagnostics[0].message.startswith("branch 'validate.done[save]': invalid context entry 'bogus': expected")
    assert diagnostics[1].message.startswith("branch 'validate.done[save]': invalid context entry 'steps.read.text'")
    assert diagnostics[2].message == ("branch 'validate.done[save]': context entry 'steps.nope.outputs' names unknown "
                                      "step 'nope' (steps: read, validate, fix, save)")


def test_context_naming_a_step_that_may_not_have_run_is_an_error():
    """`fix` has not run on the first pass through `validate.done`; `save` never runs before it."""
    text = with_context("steps.fix.outputs, steps.save.summary")
    diagnostics = [d for d in check_agentic_edges(loaded(text), EdgesLock()) if d.code == "E-CHECK-CONTEXT"]
    assert [(format_loc(d.loc), d.message) for d in diagnostics] == [
        ("edges[1].to[0].context[0]", "branch 'validate.done[save]': context entry 'steps.fix.outputs': step 'fix' "
                                      "has not completed on every path reaching 'validate.done'"),
        ("edges[1].to[0].context[1]", "branch 'validate.done[save]': context entry 'steps.save.summary': step 'save' "
                                      "has not completed on every path reaching 'validate.done'"),
    ]


def test_valid_context_entries_are_accepted():
    entries = ("steps.read.outputs, steps.read.outputs.value, steps.validate.summary, previous.outputs, "
               "previous.summary, process.goal, process.inputs, full_trace")
    assert only(with_context(entries), "E-CHECK-CONTEXT") == []


def test_a_when_guard_makes_a_step_completed_for_the_check():
    """The check runs only when `when:` holds, so `steps.fix.runs > 0` means `fix` has completed (exit-aware)."""
    assert only(with_context("steps.fix.outputs", when="steps.fix.runs > 0"), "E-CHECK-CONTEXT") == []
    assert only(with_context("steps.fix.outputs", when="steps.fix.runs < 5"), "E-CHECK-CONTEXT") == [
        "edges[1].to[0].context[0]",
    ]


# --- W-AGENTIC-NO-ELSE -----------------------------------------------------------------------------------------------

def test_agentic_edge_without_an_else_warns():
    edge = AGENTIC.replace("      - step: $exit.rejected\n        with: { value: steps.validate.outputs.value }\n", "")
    text = process(edge)
    diagnostics = check_agentic_edges(loaded(text), edges_lock(LOCKED))
    assert [(d.code, d.severity, format_loc(d.loc)) for d in diagnostics] == [
        ("W-AGENTIC-NO-ELSE", "warning", "edges[1].to"),
    ]
    assert diagnostics[0].message == ("agentic edge 'validate.done' has no else branch: a negative verdict routes to "
                                      "the process error handler")


def test_agentic_edge_with_an_else_does_not_warn():
    assert only(process(), "W-AGENTIC-NO-ELSE") == []


# --- W-AGENTIC-EDGE-FAST ---------------------------------------------------------------------------------------------

def test_agentic_edge_in_a_fast_process_warns():
    assert findings(process(latency="fast")) == [("W-AGENTIC-EDGE-FAST", "edges[1].kind")]


@pytest.mark.parametrize("latency", ["normal", None])
def test_agentic_edge_in_a_normal_process_does_not_warn(latency):
    assert only(process(latency=latency), "W-AGENTIC-EDGE-FAST") == []


# --- W-EDGE-LOCK-MISSING ---------------------------------------------------------------------------------------------

def test_agentic_branch_without_a_lock_entry_warns():
    text = process()
    diagnostics = check_agentic_edges(loaded(text), EdgesLock())
    assert [(d.code, format_loc(d.loc)) for d in diagnostics] == [("W-EDGE-LOCK-MISSING", "edges[1].to[0].check")]
    assert diagnostics[0].message == (
        "agentic branch 'validate.done[save]' has no entry in edges.lock.yaml: defaults used at run time (cheap, low, "
        "2 retries); run `wynd compile` to lock"
    )


def test_shorthand_agentic_edge_is_keyed_by_index_and_located_at_the_edge_level_check():
    edge = """  - from: validate.done
    kind: agentic
    to: save
    check: The document is a final supplier invoice.
    with: { value: steps.validate.outputs.value }
"""
    text = process(edge)
    diagnostics = check_agentic_edges(loaded(text), EdgesLock())
    assert [(d.code, format_loc(d.loc)) for d in diagnostics] == [
        ("W-AGENTIC-NO-ELSE", "edges[1].to"),
        ("W-EDGE-LOCK-MISSING", "edges[1].to[0].check"),
    ]
    assert "'validate.done[0]'" in diagnostics[1].message
    assert diagnostics[1].line == line_of(text, "check: The document")


def test_locked_agentic_branch_does_not_warn_missing():
    assert only(process(), "W-EDGE-LOCK-MISSING") == []


def test_checks_after_the_else_are_not_agentic_branches():
    edge = AGENTIC + "      - step: fix\n        check: Never evaluated.\n"
    assert findings(process(edge), LOCKED) == []


# --- W-EDGE-LOCK-STALE -----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("locked_check, locked_context", [
    ("The document is a supplier invoice.", CONTEXT),          # check text edited since
    (CHECK, None),                                             # context added since
])
def test_edited_check_or_context_is_stale(locked_check, locked_context):
    lock = f"wynd: 1\nedges:\n  validate.done[save]:\n    check_hash: '{check_hash(locked_check, locked_context)}'\n"
    text = process()
    diagnostics = check_agentic_edges(loaded(text), edges_lock(lock))
    assert [(d.code, format_loc(d.loc), d.file) for d in diagnostics] == [
        ("W-EDGE-LOCK-STALE", "edges[1].to[0].check", PATH),
    ]
    assert diagnostics[0].message == (
        "agentic branch 'validate.done[save]': check edited since it was locked; process cassettes will miss; "
        "re-run `wynd compile` or `wynd test --live`"
    )


def test_reflowed_check_is_not_stale():
    edge = AGENTIC.replace(f"        check: {CHECK}\n",
                           "        check: >-\n          The document is a final\n          supplier   invoice.\n")
    assert findings(process(edge)) == []


# --- W-EDGE-LOCK-ORPHAN ----------------------------------------------------------------------------------------------

def test_lock_entry_without_an_agentic_branch_warns_in_the_lock_file():
    lock = LOCKED + f"  validate.done[gone]:\n    check_hash: '{check_hash('Gone.', None)}'\n"
    diagnostics = check_agentic_edges(loaded(process()), edges_lock(lock))
    assert [(d.code, format_loc(d.loc), d.file, d.line) for d in diagnostics] == [
        ("W-EDGE-LOCK-ORPHAN", "edges.validate.done[gone]", LOCK_PATH, entry_line(lock, "validate.done[gone]")),
    ]
    assert diagnostics[0].message == "edges.lock.yaml entry 'validate.done[gone]' has no matching agentic branch"


def test_lock_entry_of_a_deterministic_edge_or_a_branch_after_the_else_is_an_orphan():
    edge = AGENTIC + "      - step: fix\n        name: late\n        check: Never evaluated.\n"
    lock = LOCKED + f"  validate.done[late]:\n    check_hash: '{check_hash('Never evaluated.', None)}'\n"
    assert findings(process(edge), lock) == [("W-EDGE-LOCK-ORPHAN", "edges.validate.done[late]")]
    deterministic = process(AGENTIC.replace("    kind: agentic\n", ""))
    assert findings(deterministic, LOCKED) == [
        ("E-CHECK-NOT-AGENTIC", "edges[1].to[0].check"),
        ("W-EDGE-LOCK-ORPHAN", "edges.validate.done[save]"),
    ]


def test_lock_entries_of_agentic_branches_are_not_orphans():
    assert only(process(), "W-EDGE-LOCK-ORPHAN") == []


def test_orphan_without_a_source_map_names_the_lock_file():
    lock = EdgesLock.model_validate({"edges": {"x.done[0]": {"check_hash": check_hash("x", None)}}})
    [found] = [d for d in check_agentic_edges(loaded(process()), lock) if d.code == "W-EDGE-LOCK-ORPHAN"]
    assert (found.file, found.line, found.process) == (LOCK_PATH, None, PID)


# --- W-EDGE-PROVIDER-UNKNOWN -----------------------------------------------------------------------------------------

def test_unknown_provider_override_warns_in_the_lock_file():
    lock = LOCKED + "    provider: no-such-provider\n"
    diagnostics = check_agentic_edges(loaded(process()), edges_lock(lock))
    assert [(d.code, format_loc(d.loc), d.file, d.line) for d in diagnostics] == [
        ("W-EDGE-PROVIDER-UNKNOWN", "edges.validate.done[save].provider", LOCK_PATH,
         line_of(lock, "no-such-provider")),
    ]
    assert diagnostics[0].message == ("agentic branch 'validate.done[save]': provider 'no-such-provider' is not "
                                      "installed (entry point group wynd.providers)")


@pytest.mark.parametrize("provider", ["fake", "claude-code", "anthropic"])
def test_registered_provider_override_does_not_warn(provider):
    assert only(process(), "W-EDGE-PROVIDER-UNKNOWN", LOCKED + f"    provider: {provider}\n") == []


# --- through validate_process ----------------------------------------------------------------------------------------

@pytest.fixture
def agentic_ws(make_repo):
    return make_repo(files={
        "wynd.yaml": "process_roots: [processes]\n",
        PATH: process(),
        **{f"processes/main/proto/{name}.yaml": PROTO.format(name=name)
           for name in ("read", "validate", "fix", "save")},
    })


def agentic_findings(ws: Path) -> list[tuple[str, str, str | None, int | None]]:
    report = validate_process(load_workspace(ws), PID)
    return [(d.code, format_loc(d.loc), d.file, d.line) for d in report.diagnostics if d.code in PLAN_CODES]


def test_validate_process_reports_a_missing_lock_then_accepts_the_synced_lock(agentic_ws, write_files):
    text = (agentic_ws / PATH).read_text()
    assert agentic_findings(agentic_ws) == [
        ("W-EDGE-LOCK-MISSING", "edges[1].to[0].check", PATH, line_of(text, "check: The document")),
    ]

    lock, changed = sync_edge_lock(agentic_ws / "processes/main", load_workspace(agentic_ws).load_process(PID).doc)
    assert changed
    write_files(agentic_ws, {LOCK_PATH: dump_lock(lock)})
    report = validate_process(load_workspace(agentic_ws), PID)
    assert report.ok
    assert [(d.code, format_loc(d.loc)) for d in report.diagnostics] == [
        ("I201", "edges[1].to[1]"),
        ("I201", "edges[2].to[0]"),
    ]


def test_validate_process_reads_the_lock_through_the_tree(agentic_ws, write_files):
    lock = LOCKED + f"  validate.done[gone]:\n    check_hash: '{check_hash('Gone.', None)}'\n"
    write_files(agentic_ws, {LOCK_PATH: lock})
    assert agentic_findings(agentic_ws) == [
        ("W-EDGE-LOCK-ORPHAN", "edges.validate.done[gone]", LOCK_PATH, entry_line(lock, "validate.done[gone]")),
    ]

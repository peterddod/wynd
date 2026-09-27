"""`check_types` over typed sites (PLAN §6.1 typecheck row): codes, classification, positions and what is not
repeated from other passes. Golden cases per code live in `typecheck/`."""

from pathlib import Path

import pytest

from wynd.process.validation import validate_process
from wynd.process.validation.dataflow import analyse, typed_sites
from wynd.process.validation.exprcheck import TypedSite
from wynd.process.validation.structure import normalise, step_ifaces
from wynd.process.validation.typecheck import check_types
from wynd.process.workspace import load_workspace
from wynd.runtime.providers import ProviderInfo
from wynd.spec.errors import format_loc
from wynd.spec.expr.analysis import TypeEnv
from wynd.spec.fragments import EnvFragment

BASE = Path(__file__).parent / "typecheck" / "fixtures" / "base"
PROCESS = "processes/main/process.yaml"
TYPE_CODES = {"E-TYPE-OP", "W-TYPE-NULL", "E-TYPE-ASSIGN", "W-TYPE-ASSIGN"}
EMPTY = TypeEnv(steps={}, edges={}, process_inputs={})

HEAD = """kind: process
name: main
entry: first
env:
  vars: { OUT_DIR: where results go }
inputs:
  text: string
  count: integer
outputs:
  result: string
steps:
  first:  { use: ./steps/first }
  second: { use: ./steps/second }
"""


def catalog(name: str) -> ProviderInfo:
    return ProviderInfo(name, "agent", {}, EnvFragment())


def process(first_edge_with: str, tail: str = "") -> str:
    return HEAD + tail + f"""edges:
  - from: first.done
    to: second
    with: {first_edge_with}
  - from: second.done
    to: $exit.done
    with: {{ result: steps.second.outputs.result }}
"""


def report_for(make_repo, text: str, files: dict | None = None):
    ws = make_repo(source=BASE, files={PROCESS: text, **(files or {})})
    return validate_process(load_workspace(ws), "main", providers=catalog)


def rows(report) -> list[tuple]:
    return [(d.code, d.severity, format_loc(d.loc)) for d in report.diagnostics]


def loaded(make_repo, text: str | None = None):
    ws = make_repo(source=BASE, files={PROCESS: text} if text else None)
    return load_workspace(ws).load_process("main")


def sites_of(lp) -> list[TypedSite]:
    ifaces = step_ifaces(lp)
    norm = normalise(lp.doc)
    return typed_sites(lp.id, norm, analyse(norm, ifaces), ifaces)


# --- through the validator ------------------------------------------------------------------------------------------

def test_the_dogfood_has_no_type_findings(make_repo, repo_root):
    ws = load_workspace(make_repo(source=repo_root / "examples/invoices"))
    lp = ws.load_process("process_supplier_invoice")
    assert check_types(lp, sites_of(lp)) == []
    report = validate_process(ws, "process_supplier_invoice", providers=catalog)
    assert not {d.code for d in report.diagnostics} & TYPE_CODES


def test_env_vars_are_nullable_unless_declared(make_repo):
    report = report_for(make_repo, process("{ value: env.OUT_DIR, amount: steps.first.outputs.total }"))
    assert rows(report) == []
    report = report_for(make_repo, process("{ value: env.ELSEWHERE, amount: steps.first.outputs.total }"))
    assert rows(report) == [("W-TYPE-NULL", "warning", "edges[0].to[0].with.value")]
    assert report.ok
    guarded = process("""{ value: 'default(env.ELSEWHERE, "x")', amount: steps.first.outputs.total }""")
    assert rows(report_for(make_repo, guarded)) == []


def test_nested_with_values_are_checked_per_leaf(make_repo):
    target = """kind: proto_step
name: second
instruction: Take a record.
inputs:
  value: string
  amount: number
  record: { total: number, day: date }
outputs:
  result: string
examples:
  - inputs: { value: A, amount: 1, record: { total: 1, day: 2026-10-01 } }
    outputs: { result: done }
"""
    text = process("{ value: steps.first.outputs.value, amount: steps.first.outputs.size, "
                   "record: { total: steps.first.outputs.value, day: steps.first.outputs.day } }")
    report = report_for(make_repo, text, {"processes/main/proto/second.yaml": target})
    [d] = report.diagnostics
    assert (d.code, format_loc(d.loc)) == ("E-TYPE-ASSIGN", "edges[0].to[0].with.record.total")
    assert d.message == "input 'record.total' of step 'second': expected number got string"


def test_whole_object_bindings_report_the_nested_path(make_repo):
    target = """kind: proto_step
name: second
instruction: Take the fields.
inputs:
  value: string
  amount: number
  fields: { total: integer, missing: string }
outputs:
  result: string
examples:
  - inputs: { value: A, amount: 1, fields: { total: 1, missing: x } }
    outputs: { result: done }
"""
    text = process("{ value: steps.first.outputs.value, amount: 1, fields: steps.first.outputs }")
    [d] = report_for(make_repo, text, {"processes/main/proto/second.yaml": target}).diagnostics
    assert (d.code, d.severity) == ("E-TYPE-ASSIGN", "error")
    assert d.message == "input 'fields' of step 'second': missing missing"


def test_positions(make_repo):
    text = process("{ value: steps.first.outputs.value, amount: 'steps.first.outputs.value * 2' }")
    # a failed operation infers ANY, so it is not also an assignment error
    [op] = report_for(make_repo, text).diagnostics
    line = text.splitlines().index("    with: { value: steps.first.outputs.value, amount: 'steps.first.outputs.value * 2' }")
    column = text.splitlines()[line].index("'steps.first") + 1
    # the operator finding sits inside the expression (exact column past the quote), with its span
    assert (op.code, op.severity, format_loc(op.loc)) == ("E-TYPE-OP", "error", "edges[0].to[0].with.amount")
    assert (op.file, op.process) == (PROCESS, "main")
    assert (op.line, op.column, op.span) == (line + 1, column + 1, (0, 29))
    assert op.message == "operator '*' needs numbers, got string and integer"


def test_assignment_findings_sit_on_the_value(make_repo):
    text = process("{ value: steps.first.outputs.total, amount: steps.first.outputs.total }")
    [d] = report_for(make_repo, text).diagnostics
    line = next(i for i, t in enumerate(text.splitlines(), 1) if "value: steps.first.outputs.total" in t)
    assert (d.code, d.file, d.process, d.line) == ("E-TYPE-ASSIGN", PROCESS, "main", line)
    assert d.column == text.splitlines()[line - 1].index("steps.first.outputs.total") + 1
    assert d.span is None


def test_reference_errors_are_not_repeated_as_type_findings(make_repo):
    text = process("{ value: steps.second.outputs.result, amount: 'steps.first.outputs.nope * 2' }")
    assert sorted(rows(report_for(make_repo, text))) == [
        ("E-REF-FIELD", "error", "edges[0].to[0].with.amount"),
        ("E-REF-UNRUN", "error", "edges[0].to[0].with.value"),
    ]


def test_on_error_missing_fields_stay_with_e223(make_repo):
    handler = """kind: proto_step
name: handler
instruction: Open a ticket.
inputs:
  message: string
  ticket_id: string
  note: string?
outputs:
  result: string
examples:
  - inputs: { message: boom, ticket_id: T-1 }
    outputs: { result: T-1 }
"""
    text = process("{ value: steps.first.outputs.value, amount: steps.first.outputs.total }",
                   "  handler: { use: ./steps/handler }\non_error: handler\n")
    report = report_for(make_repo, text, {"processes/main/proto/handler.yaml": handler})
    assert rows(report) == [("E223", "error", "on_error")]


def test_handler_exit_missing_output(make_repo):
    handler = """kind: proto_step
name: handler
instruction: Report.
inputs:
  message: string
outputs: {}
examples:
  - inputs: { message: boom }
"""
    text = process("{ value: steps.first.outputs.value, amount: steps.first.outputs.total }",
                   "  handler: { use: ./steps/handler }\non_error: handler\n")
    [d] = report_for(make_repo, text, {"processes/main/proto/handler.yaml": handler}).diagnostics
    assert (d.code, format_loc(d.loc)) == ("E-TYPE-ASSIGN", "on_error")
    assert d.message == "on_error step 'handler' exit 'done' -> process output 'done': missing result"


def test_finally_by_name_names_the_field(make_repo):
    notify = """kind: proto_step
name: notify
instruction: Notify.
inputs:
  text: integer
  run_id: string
  count: string
outputs: {}
examples:
  - inputs: { text: 1, run_id: r, count: "1" }
"""
    text = process("{ value: steps.first.outputs.value, amount: steps.first.outputs.total }",
                   "  notify: { use: ./steps/notify }\nfinally:\n  - step: notify\n")
    report = report_for(make_repo, text, {"processes/main/proto/notify.yaml": notify})
    assert sorted((d.code, d.message) for d in report.diagnostics) == [
        ("E-TYPE-ASSIGN", "process input 'count' -> input 'count' of finally step 'notify': expected string got integer"),
        ("E-TYPE-ASSIGN", "process input 'text' -> input 'text' of finally step 'notify': expected integer got string"),
    ]
    assert {format_loc(d.loc) for d in report.diagnostics} == {"finally[0]"}


# --- check_types directly -------------------------------------------------------------------------------------------

def entry_site(src: dict | None, dst: dict | None) -> TypedSite:
    return TypedSite("main", ("inputs", "text"), None, EMPTY, "entry", dst, src)


@pytest.mark.parametrize(("src", "dst", "code"), [
    ({"type": "boolean"}, {"type": "string"}, "E-TYPE-ASSIGN"),
    ({"type": "number"}, {"type": "integer"}, "W-TYPE-ASSIGN"),
    ({"anyOf": [{"type": "string"}, {"type": "null"}]}, {"type": "string"}, "W-TYPE-NULL"),
    ({"type": "object", "properties": {"a": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                       "b": {"anyOf": [{"type": "integer"}, {"type": "null"}]}}},
     {"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "integer"}}}, "W-TYPE-NULL"),
    ({"anyOf": [{"type": "integer"}, {"type": "string"}, {"type": "null"}]}, {"type": "integer"}, "W-TYPE-ASSIGN"),
    ({"type": "string"}, {"type": "string", "format": "date"}, "W-TYPE-ASSIGN"),
    ({"type": "integer"}, {"type": "number"}, None),
    ({"type": "string", "format": "path"}, {"type": "string"}, None),
    ({}, {"type": "string"}, None),
    ({"type": "string"}, {}, None),
    ({"type": "boolean"}, None, None),
    (None, {"type": "string"}, None),
])
def test_classification(make_repo, src, dst, code):
    lp = loaded(make_repo)
    found = check_types(lp, [entry_site(src, dst)])
    assert [d.code for d in found] == ([code] if code else [])
    for d in found:
        assert (d.process, d.file, format_loc(d.loc)) == ("main", PROCESS, "inputs.text")
        assert d.message.startswith("process input 'text' -> input 'text' of entry step 'first': ")


def test_condition_and_limit_sites_only_report_operator_findings(make_repo):
    lp = loaded(make_repo)
    when = TypedSite("main", ("edges", 0, "to", 0, "when"), '"a" > 1', EMPTY, "when", None)
    limit = TypedSite("main", ("edges", 0, "to", 0, "limits", "timeout"), "3 * 2", EMPTY, "limit", None)
    found = check_types(lp, [when, limit])
    assert [(d.code, format_loc(d.loc), d.span) for d in found] == [("E-TYPE-OP", "edges[0].to[0].when", (0, 7))]


def test_unparseable_and_unknown_expressions_are_left_to_other_passes(make_repo):
    lp = loaded(make_repo)
    sites = [
        TypedSite("main", ("edges", 0, "to", 0, "with", "value"), "1 +", EMPTY, "with", {"type": "string"}),
        TypedSite("main", ("edges", 0, "to", 0, "with", "value"), "nosuch(1)", EMPTY, "with", {"type": "string"}),
    ]
    assert check_types(lp, sites) == []


def test_base_is_clean(make_repo):
    lp = loaded(make_repo)
    assert check_types(lp, sites_of(lp)) == []
    assert rows(validate_process(load_workspace(make_repo(source=BASE)), "main", providers=catalog)) == []

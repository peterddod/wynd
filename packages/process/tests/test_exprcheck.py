"""Reference checks at typed sites, env_refs, positions, and the web editor's `check_expr_at` (PLAN §6.1, §6.2)."""

import json

import pytest

from wynd.process.validation import validate_process
from wynd.process.validation.exprcheck import ExprCheckResult, check_expr_at, model_loc
from wynd.process.workspace import load_workspace
from wynd.runtime.providers import ProviderInfo
from wynd.spec.errors import format_loc
from wynd.spec.fragments import EnvFragment
from wynd.spec.yamlio import yaml_to_json

PID = "process_supplier_invoice"
PROCESS = f"processes/{PID}/process.yaml"
READ_PROTO = f"processes/{PID}/proto/read_pdf.yaml"


def catalog(name: str) -> ProviderInfo:
    return ProviderInfo(name, "agent", {}, EnvFragment())


PROTO = """kind: proto_step
name: {name}
instruction: Do {name}.
inputs:
  value: string
exits: [done, other]
outputs:
  done: {{ value: string }}
  other: {{ note: string }}
examples:
  - inputs: {{ value: a }}
    outputs: {{ value: a }}
  - inputs: {{ value: b }}
    outputs: {{ note: b }}
    exit: other
"""

GUESS = """kind: proto_step
name: guess
instruction: Guess.
examples:
  - inputs: { value: a }
    outputs: { label: x }
"""

PROCESS_YAML = """kind: process
name: p
env:
  vars: { OUT_DIR: where results go }
entry: a
inputs:
  value: string
outputs:
  value: string
steps:
  a: { use: ./steps/a }
  b: { use: ./steps/b }
  g: { use: ./steps/guess }
  f: { use: ./steps/b }
edges:
  - from: a.done
    to:
      - step: b
        name: go
        when: env.GATE == "on"
        with: { value: env.OUT_DIR }
        limits: { timeout: env.TIMEOUT }
      - step: g
        with: { value: previous.outputs.value }
  - from: a.other
    to: $exit.done
    with: { value: steps.a.outputs.nope }
  - from: b.done
    to: $exit.done
    with: { value: 'lenn(steps.b.outputs.value)' }
  - from: b.other
    to: $exit.done
    with: { value: steps.b.outputs.note }
  - from: g.done
    to: g
    with: { value: previous.outputs.lable }
finally:
  - step: f
    with: { value: 'coalesce(env.OUT_DIR, "")' }
"""


@pytest.fixture
def small(make_repo):
    return make_repo(files={
        "wynd.yaml": "process_roots: [processes]\n",
        "processes/p/process.yaml": PROCESS_YAML,
        "processes/p/proto/a.yaml": PROTO.format(name="a"),
        "processes/p/proto/b.yaml": PROTO.format(name="b"),
        "processes/p/proto/guess.yaml": GUESS,
    })


def test_findings_positions_and_env_refs(small):
    report = validate_process(load_workspace(small), "p", providers=catalog)
    found = {(d.code, d.severity, format_loc(d.loc)): d for d in report.diagnostics}
    assert [(d.code, d.severity, format_loc(d.loc)) for d in report.diagnostics] == [
        ("W202", "warning", "steps.g"),
        ("E-REF-FIELD", "error", "edges[1].to[0].with.value"),
        ("E-EXPR-FUNC", "error", "edges[2].to[0].with.value"),      # from check_process_doc only, not repeated
        ("I201", "info", "edges[4].to[0]"),
        ("E-REF-FIELD", "warning", "edges[4].to[0].with.value"),    # previous of an examples-only step
    ]
    exact = found["E-REF-FIELD", "error", "edges[1].to[0].with.value"]
    assert (exact.line, exact.column, exact.span) == (27, 20, (0, 20))
    assert "(expression" not in exact.message
    provisional = found["E-REF-FIELD", "warning", "edges[4].to[0].with.value"]
    assert provisional.message.endswith(" [provisional: interface inferred from examples]")
    assert (provisional.line, provisional.column) == (36, 20)

    assert report.env_refs == {
        "GATE": ["edge:p:a.done[go].when"],
        "OUT_DIR": ["edge:p:a.done[go].value", "finally:p:f.value"],
        "TIMEOUT": ["edge:p:a.done[go].limits.timeout"],
    }


def test_block_scalar_positions_fall_back_to_the_scalar_start(make_repo):
    block = "with:\n      value: |\n        steps.a.outputs.note +\n        steps.a.outputs.nope\n"
    text = PROCESS_YAML.replace("with: { value: steps.a.outputs.nope }\n", block)
    ws = make_repo(files={
        "wynd.yaml": "process_roots: [processes]\n",
        "processes/p/process.yaml": text,
        "processes/p/proto/a.yaml": PROTO.format(name="a"),
        "processes/p/proto/b.yaml": PROTO.format(name="b"),
        "processes/p/proto/guess.yaml": GUESS,
    })
    report = validate_process(load_workspace(ws), "p", providers=catalog)
    [d] = [d for d in report.diagnostics if d.code == "E-REF-FIELD" and d.severity == "error"]
    assert d.message.endswith("(expression 2:1)")
    assert d.snippet == "steps.a.outputs.nope\n^"
    assert (d.line, d.column) == (28, 14)


# --- check_expr_at ----------------------------------------------------------------------------------------------------

@pytest.fixture
def dogfood(make_repo, repo_root):
    ws = load_workspace(make_repo(source=repo_root / "examples/invoices"))
    doc, problem = yaml_to_json((ws.root / PROCESS).read_text(), PROCESS)
    assert problem is None
    return ws, doc


def test_check_expr_at_the_web_example(dogfood):
    ws, doc = dogfood
    loc = ["edges", 3, "to", 0, "with", "dest"]
    expr = "if steps.validate.outputs.fields.totl > 10000 then env.REVIEW_DIR else env.RECORDS_DIR"
    result = check_expr_at(ws, PID, doc, {}, loc, expr)
    assert isinstance(result, ExprCheckResult)
    [error] = result.errors
    assert (error.code, error.span, error.loc, error.process) == ("E-REF-FIELD", (3, 37), tuple(loc), PID)
    assert result.warnings == []
    scope = result.scope
    for ref in ("process.inputs.pdf_path", "run.id", "previous.outputs", "previous.summary", "steps.read.outputs.text",
                "steps.read.exit", "steps.fix.runs", "steps.extract.outputs.total", "steps.validate.outputs.fields",
                'edges["validate.done"][2].taken', "env.REVIEW_DIR", "env.RECORDS_DIR", "env.ESCALATIONS_DIR"):
        assert ref in scope, ref
    assert "steps.fix.outputs.total" not in scope          # fix may not have run here
    assert "steps.save.outputs.path" not in scope
    assert len(scope) == len(set(scope))

    ok = check_expr_at(ws, PID, doc, {}, loc, "steps.validate.outputs.fields.total")
    assert (ok.errors, ok.warnings) == ([], [])


def test_check_expr_at_shorthand_loc_and_in_memory_protos(dogfood):
    ws, doc = dogfood
    assert model_loc(doc, ("edges", 0, "with", "invoice_text")) == ("edges", 0, "to", 0, "with", "invoice_text")
    assert model_loc(doc, ("edges", 3, "to", 1, "when")) == ("edges", 3, "to", 1, "when")
    loc = ["edges", 0, "with", "invoice_text"]
    assert check_expr_at(ws, PID, doc, {}, loc, "steps.read.outputs.text").errors == []

    proto, _ = yaml_to_json((ws.root / READ_PROTO).read_text(), READ_PROTO)
    proto["outputs"] = {"content": "string", "pages": "integer"}
    proto["examples"] = []
    result = check_expr_at(ws, PID, doc, {READ_PROTO: proto}, loc, "steps.read.outputs.text")
    assert [e.code for e in result.errors] == ["E-REF-FIELD"]
    assert "steps.read.outputs.content" in result.scope


def test_check_expr_at_the_edited_when_refines_its_own_with(dogfood):
    ws, doc = dogfood
    doc = json.loads(json.dumps(doc))
    doc["edges"][3]["to"][2]["when"] = 'steps.fix.exit == "done"'
    result = check_expr_at(ws, PID, doc, {}, ["edges", 3, "to", 2, "with", "fields"], "steps.fix.outputs.total")
    assert result.errors == []
    result = check_expr_at(ws, PID, doc, {}, ["edges", 3, "to", 2, "when"], "steps.fix.outputs.total > 0")
    assert [e.code for e in result.errors] == ["E-REF-UNRUN"]


def test_check_expr_at_syntax_errors_keep_the_scope(dogfood):
    ws, doc = dogfood
    result = check_expr_at(ws, PID, doc, {}, ["edges", 3, "to", 0, "with", "dest"], "steps.validate.outputs.")
    assert [e.code for e in result.errors] == ["E-EXPR-SYNTAX"]
    assert result.errors[0].span is not None
    assert "steps.validate.outputs.record" in result.scope


def test_check_expr_at_falls_back_without_process_context(dogfood):
    ws, doc = dogfood
    broken = {**doc, "entry": 42}
    result = check_expr_at(ws, PID, broken, {}, ["edges", 0, "to", 0, "with", "x"], "lenn(steps.read.outputs.text)")
    assert ([e.code for e in result.errors], result.scope) == (["E-EXPR-FUNC"], [])
    result = check_expr_at(ws, "nope", doc, {}, ["edges", 0, "to", 0, "when"], "steps.read.runs > 0")
    assert (result.errors, result.scope) == ([], [])
    result = check_expr_at(ws, PID, doc, {}, ["edges", 99, "to", 0, "when"], "steps.nope.runs > 0")
    assert (result.errors, result.scope) == ([], [])

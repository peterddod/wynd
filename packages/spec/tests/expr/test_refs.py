"""Static analysis: references, value_references, env_names, check_expression and check_references
($DRAFTS/01 §7.9, §12.4)."""

import ast
import dataclasses
import re
from pathlib import Path

import pytest

import wynd.spec.expr.analysis
from wynd.spec.expr.analysis import (
    Ref,
    check_expression,
    check_references,
    env_names,
    references,
    value_references,
)
from wynd.spec.expr.evaluator import parse

# Every expression of the dogfood process.yaml ($DRAFTS/01 Appendix A.2).
DOGFOOD = [
    "steps.read.outputs.text",
    "steps.extract.outputs",
    "steps.validate.outputs.valid",
    "steps.validate.outputs.record",
    "if steps.validate.outputs.fields.total > 10000 then env.REVIEW_DIR\nelse env.RECORDS_DIR",
    "steps.validate.outputs.fixable and steps.fix.runs < 3",
    "steps.validate.outputs.fields",
    "steps.validate.outputs.errors",
    "steps.fix.outputs",
    "steps.save.outputs.record",
]


def codes(diagnostics) -> list[str]:
    return [d.code for d in diagnostics]


def test_references_combined_expression():
    refs = references(
        'if steps.validate.outputs.fields.total > 10000 and edges["validate.done"].retry.taken < 2 '
        "then env.REVIEW_DIR else default(steps.fix.outputs.x, run.id) + "
        "steps.extract.outputs.items[steps.fix.runs].sku"
    )
    assert [(str(r), r.guarded) for r in refs] == [
        ("steps.validate.outputs.fields.total", False),
        ("edges.validate.done.retry.taken", False),
        ("env.REVIEW_DIR", False),
        ("steps.fix.outputs.x", True),
        ("run.id", False),
        ("steps.extract.outputs.items[*].sku", False),
        ("steps.fix.runs", False),
    ]
    assert refs[1].path == ("validate.done", "retry", "taken")
    assert refs[5].path == ("extract", "outputs", "items", None, "sku")


def test_reference_fields_and_positions():
    [ref] = references('  steps.x.outputs.items[0]["k"]')
    assert ref == Ref("steps", ("x", "outputs", "items", 0, "k"), 1, 3)
    assert str(ref) == "steps.x.outputs.items[0].k"
    assert references(parse("run.id")) == [Ref("run", ("id",), 1, 1)]
    [outer, inner] = references("a.b[c.d]\n")
    assert (str(outer), str(inner)) == ("a.b[*]", "c.d")
    assert (inner.line, inner.column) == (1, 5)


def test_guarded_arguments():
    refs = references("default(steps.a.outputs.x, steps.b.outputs.y) + coalesce(steps.c.outputs, steps.d.outputs)")
    assert [(r.path[0], r.guarded) for r in refs] == [("a", True), ("b", False), ("c", True), ("d", True)]
    # Guarding is structural: everything inside the first argument of default() is guarded, nesting included.
    refs = references("default(len(steps.a.outputs.items) + 1, 0) + len(steps.b.outputs.items)")
    assert [(r.path[0], r.guarded) for r in refs] == [("a", True), ("b", False)]


def test_references_through_non_chains():
    refs = references('[steps.a.runs, {k: env.X}, -steps.b.runs, (if run.id then 1 else 2), f(x).y, "s"]')
    assert [str(r) for r in refs] == ["steps.a.runs", "env.X", "steps.b.runs", "run.id", "x"]
    assert references("1 + 2") == []


def test_value_references():
    value = {
        "record": "steps.validate.outputs.record",
        "n": 3,
        "flag": True,
        "l": ["run.id", {"x": "process.inputs.pdf_path"}, None],
    }
    assert [(loc, str(ref)) for loc, ref in value_references(value)] == [
        (("record",), "steps.validate.outputs.record"),
        (("l", 0), "run.id"),
        (("l", 1, "x"), "process.inputs.pdf_path"),
    ]
    assert [(loc, str(ref)) for loc, ref in value_references("env.A")] == [((), "env.A")]
    assert value_references(1.5) == []


def test_env_names():
    assert env_names(DOGFOOD) == {"REVIEW_DIR", "RECORDS_DIR"}
    assert env_names(['env["A_B"]', "default(env.C, env.D)", "env[x]", "steps.env.outputs"]) == {"A_B", "C", "D"}
    assert env_names([]) == set()


CHECK_EXPRESSION = [
    ("steps.x.foo", "E-REF-SHAPE"),
    ("steps.x.exit.y", "E-REF-SHAPE"),
    ("steps.x.runs.y", "E-REF-SHAPE"),
    ("steps", "E-REF-SHAPE"),
    ("steps[run.id].outputs", "E-REF-SHAPE"),
    ("edges[run.id][0].taken", "E-REF-SHAPE"),
    ('edges["a.b"]', "E-REF-SHAPE"),
    ('edges["a.b"][0]', "E-REF-SHAPE"),
    ('edges["a.b"][steps.x.runs].taken', "E-REF-SHAPE"),
    ('edges["a.b"][0].count', "E-REF-SHAPE"),
    ('edges["a.b"][0].taken.x', "E-REF-SHAPE"),
    ("edges", "E-REF-SHAPE"),
    ("env", "E-REF-SHAPE"),
    ("env.A.b", "E-REF-SHAPE"),
    ("env[run.id]", "E-REF-SHAPE"),
    ("run", "E-REF-SHAPE"),
    ("run.idx", "E-REF-SHAPE"),
    ("process", "E-REF-SHAPE"),
    ("process.goal", "E-REF-SHAPE"),
    ("previous", "E-REF-SHAPE"),
    ("previous.exit", "E-REF-SHAPE"),
    ("unlabelled", "E-REF-NAME"),
    ("in", "E-REF-NAME"),
    ("lenn(1)", "E-EXPR-FUNC"),
    ("default(1)", "E-EXPR-ARITY"),
    ("now(1)", "E-EXPR-ARITY"),
    ("coalesce()", "E-EXPR-ARITY"),
    ("a <", "E-EXPR-SYNTAX"),
]


@pytest.mark.parametrize("text,code", CHECK_EXPRESSION)
def test_check_expression(text, code):
    diagnostics = check_expression(text)
    assert codes(diagnostics) == [code]
    assert all(d.severity == "error" for d in diagnostics)


def test_check_expression_draft_rows_with_bare_names():
    # $DRAFTS/01 §12.4 writes these with an undefined bare name, which is itself reported.
    assert codes(check_expression("edges[k][0].taken")) == ["E-REF-SHAPE", "E-REF-NAME"]
    assert codes(check_expression("lenn(x)")) == ["E-EXPR-FUNC", "E-REF-NAME"]


def test_check_expression_messages():
    [name] = check_expression("unlabelled")
    assert "'\"unlabelled\"'" in name.message
    assert check_expression("lenn(x)")[0].message == "unknown function 'lenn' (did you mean 'len'?)"
    assert check_expression("default(1)")[0].message == "default() takes 2 argument(s), got 1"
    assert check_expression("coalesce()")[0].message == "coalesce() takes 1..99 argument(s), got 0"
    [syntax] = check_expression("if a then b elze c")
    assert syntax.message.startswith("unexpected 'elze'; expected an operator, 'elif' or 'else'")


@pytest.mark.parametrize(
    "text",
    [
        *DOGFOOD,
        "steps.x",
        "steps.x.exit",
        "steps.x.runs",
        'steps["x"].outputs["a b"][0]',
        'edges["validate.done"][0].taken',
        'edges["validate.done"].retry.taken',
        'env["A"]',
        "env.A",
        "process.inputs",
        "run.id",
        "previous.outputs",
        "previous.summary.key_outputs.x",
        'default(steps.x.outputs.label, "unlabelled")',
        'join(split(lower(upper("a")), ","), "-") + now()',
        "{a: [1, 2.5, true, null]}",
    ],
)
def test_check_expression_accepts_valid_expressions(text):
    assert check_expression(text) == []


def test_expression_diagnostics_are_relative_to_the_expression():
    [syntax] = check_expression("a +\n  * b")
    assert (syntax.line, syntax.column, syntax.span) == (2, 3, (6, 7))
    assert (syntax.file, syntax.loc) == (None, ())
    shape, func = check_expression("1 + steps.x.foo + lenn(2)")
    assert (shape.code, shape.line, shape.column, shape.span) == ("E-REF-SHAPE", 1, 5, (4, 15))
    assert (func.code, func.column, func.span) == ("E-EXPR-FUNC", 19, (18, 25))


def test_check_expression_reports_every_finding_in_source_order():
    diagnostics = check_expression("lenn(unlabelled) + default(steps.x.foo)")
    assert codes(diagnostics) == ["E-EXPR-FUNC", "E-REF-NAME", "E-EXPR-ARITY", "E-REF-SHAPE"]


# check_references at the site validate.done[0].with.dest ($DRAFTS/01 §12.4).
SITE = [
    ("steps.validate.outputs.record", []),
    ("steps.validate.outputs.fields.total", []),
    ("steps.validate.outputs.errors[0]", []),
    ("steps.validate.exit", []),
    ("steps.fix.runs", []),
    ("steps.save.exit", []),
    ("steps.vaildate.outputs.x", ["E-REF-STEP"]),
    ("steps.extract.outputs.totl", ["E-REF-FIELD"]),
    ("steps.extract.outputs.total.x", ["E-REF-FIELD"]),
    ("steps.extract.outputs.exit", ["E-REF-FIELD"]),
    ("steps.fix.outputs.total", ["E-REF-UNRUN"]),
    ("steps.fix.outputs", ["E-REF-UNRUN"]),
    ("steps.fix.outputs.totl", ["E-REF-UNRUN", "E-REF-FIELD"]),
    ("default(steps.fix.outputs.total, 0)", []),
    ("default(steps.fix.outputs.totl, 0)", ["E-REF-FIELD"]),
    ("steps.save.outputs.record", ["E-REF-UNRUN"]),
    ("coalesce(steps.save.outputs.record, {})", []),
    ('edges["validate.done"].retry.taken', []),
    ('edges["validate.done"][2].taken', []),
    ('edges["validate.done"].nope.taken', ["E-REF-EDGE"]),
    ('edges["validate.done"][9].taken', ["E-REF-EDGE"]),
    ('edges["validate.dne"][0].taken', ["E-REF-EDGE"]),
    ("process.inputs", []),
    ("process.inputs.pdf_path", []),
    ("process.inputs.pdf", ["E-REF-INPUT"]),
    ("previous.outputs.valid", []),
    ("previous.outputs.nope", ["E-REF-FIELD"]),
    ("previous.summary", []),
    ("previous.summary.note", []),
    ("previous.summary.key_outputs.x", []),
    ("previous.summary.foo", ["E-REF-FIELD"]),
    ("previous.summary.step.x", ["E-REF-FIELD"]),
    ("env.ANYTHING", []),
    ("run.id", []),
    ("unlabelled", ["E-REF-NAME"]),
    ("steps.validate.foo", ["E-REF-SHAPE"]),
    ("steps.validate.outputs.x + lenn(1)", ["E-REF-FIELD", "E-EXPR-FUNC"]),
    ("steps.save.outputs[steps.fix.outputs.total]", ["E-REF-UNRUN", "E-REF-UNRUN"]),
    ("if a then", ["E-EXPR-SYNTAX"]),
]


@pytest.mark.parametrize("text,expected", SITE)
def test_check_references_at_site(text, expected, site_env):
    assert codes(check_references(text, site_env)) == expected


def test_check_references_accepts_the_dogfood_expressions(site_env):
    guarded = [text for text in DOGFOOD if "steps.fix.outputs" not in text and "steps.save.outputs" not in text]
    assert [check_references(text, site_env) for text in guarded] == [[] for _ in guarded]


def test_check_references_messages(site_env):
    [step] = check_references("steps.vaildate.outputs.x", site_env)
    assert step.message == "unknown step 'vaildate' (did you mean 'validate'?)"
    [field] = check_references("steps.extract.outputs.totl", site_env)
    assert field.message == "'totl' is not an output of step 'extract' (exits: done)"
    [unrun] = check_references("steps.fix.outputs.total", site_env)
    assert unrun.message.startswith("step 'fix' may not have run on every path to this expression; guard with")
    [edge] = check_references('edges["validate.dne"][0].taken', site_env)
    assert edge.message == "no edge 'validate.dne' (did you mean 'validate.done'?)"
    [branch] = check_references('edges["validate.done"][9].taken', site_env)
    assert branch.message == "edge 'validate.done' has no branch 9 (it has 3)"
    [named] = check_references('edges["validate.done"].nope.taken', site_env)
    assert named.message == "edge 'validate.done' has no branch named 'nope'"
    [inputs] = check_references("process.inputs.pdf", site_env)
    assert inputs.message == "'pdf' is not an input of the process (inputs: pdf_path)"


def test_check_references_spans(site_env):
    [field] = check_references("1 + steps.extract.outputs.totl", site_env)
    assert (field.line, field.column, field.span) == (1, 5, (4, 30))
    first, second = check_references('x\n or edges["validate.done"][9].taken', site_env)
    assert (first.code, first.span) == ("E-REF-NAME", (0, 1))
    assert (second.code, second.line, second.column, second.span) == ("E-REF-EDGE", 2, 5, (6, 37))


def test_exit_aware_references(exit_aware_env):
    [diagnostic] = check_references("steps.validate.outputs.record", exit_aware_env)
    assert diagnostic.code == "E-REF-EXIT"
    assert "'invalid'" in diagnostic.message and "'done'" not in diagnostic.message
    assert diagnostic.message == (
        "field 'record' is not declared on exit 'invalid' of step 'validate', which can reach this expression"
    )
    assert check_references("coalesce(steps.validate.outputs.record, {})", exit_aware_env) == []
    assert check_references("default(steps.validate.outputs.record, {})", exit_aware_env) == []
    assert check_references("steps.validate.outputs.valid", exit_aware_env) == []
    assert codes(check_references("previous.outputs.record", exit_aware_env)) == ["E-REF-EXIT"]
    # A field on no exit is a typo, guarded or not.
    assert codes(check_references("default(steps.validate.outputs.recrod, {})", exit_aware_env)) == ["E-REF-FIELD"]
    [typo] = check_references("steps.validate.outputs.recrod", exit_aware_env)
    assert typo.message == "'recrod' is not an output of step 'validate' (exits: done, invalid)"


def test_error_exit_references(error_edge_env):
    assert check_references("steps.read.outputs.message", error_edge_env) == []
    assert check_references("steps.read.outputs.cause", error_edge_env) == []
    assert check_references("steps.read.outputs.inputs.pdf_path", error_edge_env) == []
    assert check_references("steps.read.outputs.child.process", error_edge_env) == []
    assert check_references("previous.outputs.traceback", error_edge_env) == []
    assert codes(check_references("steps.read.outputs.text", error_edge_env)) == ["E-REF-FIELD"]
    assert codes(check_references("steps.read.outputs.exit", error_edge_env)) == ["E-REF-FIELD"]
    assert codes(check_references("steps.read.outputs.child.nope", error_edge_env)) == ["E-REF-FIELD"]


def test_previous_is_unchecked_when_unknown(site_env):
    env = dataclasses.replace(site_env, previous=None)
    assert check_references("previous.outputs.anything + previous.summary.anything", env) == []
    assert codes(check_references("previous.exit", env)) == ["E-REF-SHAPE"]


def test_emitted_codes_are_in_the_spec_namespace():
    # PLAN §3.2: the spec codes this module may emit.
    listed = {
        "E-EXPR-SYNTAX", "E-EXPR-FUNC", "E-EXPR-ARITY", "E-REF-NAME", "E-REF-SHAPE", "E-REF-STEP", "E-REF-FIELD",
        "E-REF-UNRUN", "E-REF-EXIT", "E-REF-INPUT", "E-REF-EDGE",
    }
    source = ast.parse(Path(wynd.spec.expr.analysis.__file__).read_text())
    emitted = {
        node.value
        for node in ast.walk(source)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.fullmatch(r"[EWI]-[A-Z-]+", node.value)
    }
    assert emitted == listed

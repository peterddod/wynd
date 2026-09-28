"""Evaluation: the success and error tables of $DRAFTS/01 §12.1–§12.2, the value domain (§7.4), references (§7.5),
builtins (§7.6) and the YAML-value entry points (§7.7)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from wynd.spec.expr.errors import EvalError, ExprError, ExprSyntaxError
from wynd.spec.expr.evaluator import (
    evaluate,
    evaluate_condition,
    evaluate_limit,
    evaluate_value,
    evaluate_with,
    parse,
    strict_eq,
    truthy,
)
from wynd.spec.expr.scope import StepState


def same(a, b) -> bool:
    """Equal in value and in type, recursively (1, 1.0 and True are all different)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return a == b


OK = [
    ("steps.validate.outputs.valid", False),
    ("steps.validate.outputs.fixable and steps.fix.runs < 3", True),
    ("steps.save.runs", 0),
    ("steps.save.outputs", None),
    ("steps.save.outputs.record", None),
    ("steps.save.exit", None),
    ("steps.read.exit", "done"),
    ("steps.validate.runs", 2),
    ("steps.fix", {"runs": 1, "exit": "done", "outputs": {"total": 1}}),
    ("if steps.validate.outputs.fields.total > 10000 then env.REVIEW_DIR else env.RECORDS_DIR", "/review"),
    ('if 1 > 2 then "a" elif 2 > 1 then "b" else "c"', "b"),
    ("if false then 1 elif false then 2 else 3", 3),
    ('default(steps.extract.outputs.label, "unlabelled")', "unlabelled"),
    ("default(0, 5)", 0),
    ('default("", "x")', ""),
    ("default(false, true)", False),
    ('coalesce(null, steps.extract.outputs.label, "x", "y")', "x"),
    ("coalesce(null)", None),
    ('edges["validate.done"][1].taken', 1),
    ('edges["validate.done"].retry.taken', 1),
    ('edges["validate.done"]["retry"].taken', 1),
    ('edges["validate.done"][0].taken == 0', True),
    ("process.inputs.pdf_path", "/w/in.pdf"),
    ("process.inputs", {"pdf_path": "/w/in.pdf"}),
    ("env.MISSING", None),
    ('env["REVIEW_DIR"]', "/review"),
    ('default(env.MISSING, "d")', "d"),
    ("run.id", "run-123"),
    ("previous.outputs.valid", False),
    ("previous.summary.step", "validate"),
    ("previous.summary.key_outputs.valid", False),
    ("1 + 2 * 3", 7),
    ("(1 + 2) * 3", 9),
    ("7 / 2", 3.5),
    ("4 / 2", 2.0),
    ("7 % 3", 1),
    ("-7 % 3", 2),
    ("7.5 % 2", 1.5),
    ("- 2 - -3", 1),
    ("1 + 0.5", 1.5),
    ('"a" + "b"', "ab"),
    ("'it\\'s'", "it's"),
    ('"\\u00e9"', "é"),
    ("[1] + [2]", [1, 2]),
    ("1 == 1.0", True),
    ("true == 1", False),
    ("null == null", True),
    ('"1" == 1', False),
    ("[1, true] == [1, true]", True),
    ("[1] == [true]", False),
    ("{a: 1, b: [2]} == {b: [2.0], a: 1}", True),
    ("{a: 1} != {a: 1, b: 2}", True),
    ('"2026-09-01" < "2026-10-01"', True),
    ('"b" >= "a"', True),
    ('"GBP" in ["GBP", "EUR"]', True),
    ('"x" not in ["GBP"]', True),
    ("1 in [1.0, 2]", True),
    ('"VOICE" in steps.read.outputs.text', True),
    ('"total" in steps.validate.outputs.fields', True),
    ("1 in {a: 1}", False),
    ('"a" in null', False),
    ('"a" not in null', True),
    ("not steps.validate.outputs.valid", True),
    ("not 1 == 2", True),
    ("not []", True),
    ('1 and "x"', True),
    ('0 or ""', False),
    ("false and (1 / 0)", False),
    ("true or (1 / 0)", True),
    ("len(steps.validate.outputs.errors)", 1),
    ('len("abc")', 3),
    ("len({a: 1})", 1),
    ("len(null)", 0),
    ('lower("AbC")', "abc"),
    ('upper("AbC")', "ABC"),
    ("upper(null)", None),
    ('contains(steps.validate.outputs.errors, "bad currency")', True),
    ('contains("abc", "b")', True),
    ('contains(null, "b")', False),
    ('startswith(steps.extract.outputs.invoice_number, "INV-")', True),
    ('startswith(null, "x")', False),
    ('join(["a", "b"])', "a,b"),
    ('join(["a", "b"], " ")', "a b"),
    ('join(null, "-")', ""),
    ('split("a b  c")', ["a", "b", "c"]),
    ('split("a,b", ",")', ["a", "b"]),
    ('split(null, ",")', []),
    ("now()", "2026-09-22T21:50:03Z"),
    ("steps.extract.outputs.items[0].sku", "A"),
    ("steps.extract.outputs.items[5]", None),
    ("steps.extract.outputs.items[-1].qty", 2),
    ("steps.extract.outputs.items[-2]", None),
    ("steps.extract.outputs.items[steps.fix.runs - 1].qty", 2),
    ("steps.extract.outputs.missing.deeper", None),
    ('{a: 1, "b c": [true, null]}', {"a": 1, "b c": [True, None]}),
    ("[1, 2, 3,]", [1, 2, 3]),
    ("1.5e2", 150.0),
]


@pytest.mark.parametrize("text,expected", OK)
def test_ok(text, expected, scope):
    got = evaluate(text, scope)
    assert same(got, expected), (got, expected)


ERR = [
    ("steps.validate.outputs.fields.total > null", "cannot compare number > null"),
    ("steps.save.outputs.total > 3", "cannot compare null > number"),
    ('"a" < 1', "cannot compare string < number"),
    ("true < false", "cannot compare boolean < boolean"),
    ("[1] < [2]", "cannot compare list < list"),
    ('1 + "a"', "cannot add number and string"),
    ("1 + null", "cannot add number and null"),
    ("true + 1", "cannot add boolean and number"),
    ("{} + {}", "cannot add object and object"),
    ("1 / 0", "division by zero"),
    ("1 % 0", "division by zero"),
    ("1.5 / 0.0", "division by zero"),
    ('"a" * 2', "operator '*' needs numbers, got string and number"),
    ("null - 1", "operator '-' needs numbers, got null and number"),
    ('-"a"', "cannot negate string"),
    ("-true", "cannot negate boolean"),
    ("unlabelled", "unknown name 'unlabelled'"),
    ("in", "unknown name 'in'"),
    ("frobnicate(1)", "unknown function 'frobnicate'"),
    ("len(1, 2)", "len() takes 1 argument(s), got 2"),
    ("now(1)", "now() takes 0 argument(s), got 1"),
    ("coalesce()", "coalesce() takes 1..99 argument(s), got 0"),
    ("join()", "join() takes 1..2 argument(s), got 0"),
    ("len(5)", "len() of number"),
    ("lower(1)", "lower() needs a string, got number"),
    ('startswith("a", 1)', "startswith() needs strings"),
    ("join([1, 2])", "join() needs a list of strings"),
    ('join(["a"], 1)', "join() needs a list of strings and a string separator"),
    ('split("a", "")', "separator must be a non-empty string"),
    ("split(1)", "split() needs a string, got number"),
    ('edges["nope.done"][0].taken', "unknown edge 'nope.done'"),
    ('edges["validate.done"][7].taken', "edge 'validate.done' has no branch 7"),
    ('edges["validate.done"][-1].taken', "has no branch -1"),
    ('edges["validate.done"].bogus.taken', "edge 'validate.done' has no branch named 'bogus'"),
    ('edges["validate.done"][0].count', "branch counters only have .taken, not 'count'"),
    ('edges["validate.done"][0]', "branch counters only have .taken"),
    ('edges["validate.done"]', "needs a branch"),
    ("edges", "edges needs an edge key"),
    ("edges[1]", "unknown edge 1"),
    ("steps", "steps needs a step key"),
    ("steps.nope.outputs", "unknown step 'nope'"),
    ("env", "env needs a variable name"),
    ("env[1]", "env[...] needs a string, got number"),
    ("steps.read.outputs.text.foo", "cannot access 'foo' on a string"),
    ("steps.read.runs.x", "cannot access 'x' on a number"),
    ('steps.extract.outputs.items["x"]', "list index must be an integer, got string"),
    ("steps.extract.outputs.items[true]", "list index must be an integer, got boolean"),
    ("steps.extract.outputs[0]", "object keys are strings, got number"),
    ("1 in 2", "'in' needs a string, list or object on the right, got number"),
    ('1 in "abc"', "'in' a string needs a string, got number"),
]


@pytest.mark.parametrize("text,message", ERR)
def test_errors(text, message, scope):
    with pytest.raises(EvalError) as e:
        evaluate(text, scope)
    assert message in str(e.value), str(e.value)
    assert e.value.message in str(e.value)
    assert e.value.text == text
    assert e.value.node is not None


@pytest.mark.parametrize(
    "text,position",
    [
        ("steps.validate.outputs.fields.total > null", "1:1"),
        ("true and 1 + (2 / 0) > 0", "1:15"),
        ('[1, -"a"]', "1:5"),
        ("len(steps.read.outputs.text.foo)", "1:5"),
        ("unlabelled", "1:1"),
        ('default(null, frobnicate("x"))', "1:15"),
        ("if true\n then 1 > null\n else 0", "2:7"),
    ],
)
def test_error_positions(text, position, scope):
    with pytest.raises(EvalError) as e:
        evaluate(text, scope)
    assert str(e.value).startswith(f"{position}: ")
    assert f"{e.value.node.line}:{e.value.node.column}" == position


def test_evaluating_an_ast(scope):
    node = parse("steps.fix.runs + 1")
    assert evaluate(node, scope) == 2
    with pytest.raises(EvalError) as e:
        evaluate(parse("1 / 0"), scope)
    assert e.value.text is None


def test_syntax_errors_surface_as_expr_errors(scope):
    with pytest.raises(ExprSyntaxError):
        evaluate("a <", scope)
    with pytest.raises(ExprError):
        evaluate_value({"x": "a <"}, scope)


def test_default_and_coalesce_evaluate_fallbacks_lazily(scope):
    assert evaluate("default(1, 1 / 0)", scope) == 1
    assert evaluate('coalesce("a", 1 / 0, frobnicate())', scope) == "a"
    assert evaluate("default(steps.fix.outputs.total, steps.save.outputs.total + 1)", scope) == 1
    with pytest.raises(EvalError, match="division by zero"):
        evaluate("default(null, 1 / 0)", scope)


def test_keyword_member_names(scope):
    scope.steps["x"] = StepState(1, "done", {"in": 1, "if": 2})
    assert evaluate("steps.x.outputs.in + steps.x.outputs.if", scope) == 3


def test_previous_is_null_without_a_completed_step(scope):
    scope.previous = None
    assert evaluate("previous.outputs.valid", scope) is None
    assert evaluate("previous.summary", scope) is None
    assert evaluate("default(previous.outputs, {})", scope) == {}


def test_now_uses_the_scope_clock_in_utc(scope):
    scope.clock = lambda: datetime(2026, 9, 22, 23, 50, 3, 999999, tzinfo=timezone(timedelta(hours=2)))
    assert evaluate("now()", scope) == "2026-09-22T21:50:03Z"


def test_results_are_json_values(scope):
    assert same(evaluate("steps.extract.outputs", scope), scope.steps["extract"].outputs)
    assert same(evaluate('{r: steps.read.runs, n: [null, 1.5, "s"]}', scope), {"r": 1, "n": [None, 1.5, "s"]})


def test_evaluate_value_walks_with_trees(scope):
    value = {
        "record": "steps.validate.outputs.fields",
        "n": 3,
        "flag": True,
        "none": None,
        "f": 1.5,
        "day": date(2026, 10, 1),
        "at": datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc),
        "nested": {"d": "env.RECORDS_DIR", "l": ["run.id", 1.5, '"text"']},
    }
    assert same(
        evaluate_value(value, scope),
        {
            "record": {"total": 12000},
            "n": 3,
            "flag": True,
            "none": None,
            "f": 1.5,
            "day": "2026-10-01",
            "at": "2026-10-01T09:30:00+00:00",
            "nested": {"d": "/records", "l": ["run-123", 1.5, "text"]},
        },
    )
    assert evaluate_value("steps.read.runs", scope) == 1
    assert evaluate_value(7, scope) == 7


def test_evaluate_with(scope):
    bound = evaluate_with({"fields": "steps.validate.outputs.fields", "errors": "steps.validate.outputs.errors"}, scope)
    assert bound == {"fields": {"total": 12000}, "errors": ["bad currency"]}
    assert evaluate_with({}, scope) == {}
    with pytest.raises(EvalError, match="unknown name 'unlabelled'"):
        evaluate_with({"label": "unlabelled"}, scope)


def test_evaluate_condition(scope):
    assert evaluate_condition(None, scope) is True
    assert evaluate_condition(True, scope) is True
    assert evaluate_condition(False, scope) is False
    assert evaluate_condition("steps.validate.outputs.fixable and steps.fix.runs < 3", scope) is True
    assert evaluate_condition("steps.save.outputs", scope) is False
    assert evaluate_condition("steps.validate.outputs.errors", scope) is True
    assert evaluate_condition('""', scope) is False


def test_evaluate_limit(scope):
    assert evaluate_limit(None, scope) is None
    assert evaluate_limit(3, scope) == 3
    assert evaluate_limit(2.5, scope) == 2.5
    assert same(evaluate_limit("steps.fix.runs + 2", scope), 3)
    assert same(evaluate_limit("steps.fix.runs * 1.5", scope), 1.5)
    for text, kind in (('"10"', "string"), ("true", "boolean"), ("steps.save.outputs", "null")):
        with pytest.raises(EvalError) as e:
            evaluate_limit(text, scope)
        assert e.value.message == f"a limit must evaluate to a number, got {kind}"
        assert e.value.text == text


def test_truthy():
    falsy = [None, False, 0, 0.0, "", [], {}]
    truthy_values = [True, 1, -1, 0.1, "0", "false", [0], {"a": None}]
    assert [truthy(v) for v in falsy] == [False] * len(falsy)
    assert [truthy(v) for v in truthy_values] == [True] * len(truthy_values)


def test_strict_eq():
    assert strict_eq(1, 1.0) and strict_eq(None, None) and strict_eq("a", "a")
    assert not strict_eq(True, 1) and not strict_eq(0, False) and not strict_eq("1", 1) and not strict_eq(None, 0)
    assert strict_eq({"a": [1, {"b": 2.0}]}, {"a": [1.0, {"b": 2}]})
    assert not strict_eq({"a": [True]}, {"a": [1]})
    assert not strict_eq([1, 2], [1, 2, 3]) and not strict_eq({"a": 1}, {"b": 1})
    assert strict_eq(True, True) and not strict_eq(True, False)

from datetime import datetime, timezone
import pytest
from expr import (Scope, StepState, evaluate, evaluate_value, references, parse, EvalError, ExprSyntaxError, truthy)

def scope():
    return Scope(
        steps={
            "read": StepState(1, "done", {"text": "INVOICE 42"}),
            "extract": StepState(1, "done", {"invoice_number": "INV-1042", "total": 1200.5, "currency": "GBP", "label": None, "items": [{"sku": "A", "qty": 2}]}),
            "validate": StepState(2, "done", {"valid": False, "fixable": True, "fields": {"total": 12000}, "errors": ["bad currency"]}),
            "fix": StepState(1, "done", {"total": 1}),
            "save": StepState(),
        },
        edges={"validate.done": [0, 1, 0]},
        branch_names={"validate.done": {"retry": 1}},
        process_inputs={"pdf_path": "/w/in.pdf"},
        env={"REVIEW_DIR": "/review", "RECORDS_DIR": "/records"},
        run_id="run-123",
        previous_outputs={"valid": False},
        previous_summary={"step": "validate", "exit": "done", "key_outputs": {"valid": False}, "note": ""},
        clock=lambda: datetime(2026, 9, 22, 21, 50, 3, 123456, tzinfo=timezone.utc),
    )

OK = [
    ("steps.validate.outputs.valid", False),
    ("steps.validate.outputs.fixable and steps.fix.runs < 3", True),
    ("steps.save.runs", 0),
    ("steps.save.outputs", None),
    ("steps.save.outputs.record", None),
    ("steps.save.exit", None),
    ("steps.read.exit", "done"),
    ('if steps.validate.outputs.fields.total > 10000 then env.REVIEW_DIR else env.RECORDS_DIR', "/review"),
    ('if 1 > 2 then "a" elif 2 > 1 then "b" else "c"', "b"),
    ('if false then 1 elif false then 2 else 3', 3),
    ('default(steps.extract.outputs.label, "unlabelled")', "unlabelled"),
    ('default(0, 5)', 0),
    ('coalesce(null, steps.extract.outputs.label, "x", "y")', "x"),
    ('coalesce(null)', None),
    ('edges["validate.done"][1].taken', 1),
    ('edges["validate.done"].retry.taken', 1),
    ('edges["validate.done"][0].taken == 0', True),
    ('process.inputs.pdf_path', "/w/in.pdf"),
    ('env.MISSING', None),
    ('env["REVIEW_DIR"]', "/review"),
    ('run.id', "run-123"),
    ('previous.outputs.valid', False),
    ('previous.summary.step', "validate"),
    ('1 + 2 * 3', 7),
    ('(1 + 2) * 3', 9),
    ('7 / 2', 3.5),
    ('7 % 3', 1),
    ('-7 % 3', 2),
    ('- 2 - -3', 1),
    ('"a" + "b"', "ab"),
    ("'it\\'s'", "it's"),
    ('"\\u00e9"', "é"),
    ('[1] + [2]', [1, 2]),
    ('1 == 1.0', True),
    ('true == 1', False),
    ('null == null', True),
    ('"1" == 1', False),
    ('[1, true] == [1, true]', True),
    ('[1] == [true]', False),
    ('"GBP" in ["GBP", "EUR"]', True),
    ('"x" not in ["GBP"]', True),
    ('"VOICE" in steps.read.outputs.text', True),
    ('"total" in steps.validate.outputs.fields', True),
    ('"a" in null', False),
    ('not steps.validate.outputs.valid', True),
    ('not 1 == 2', True),
    ('not []', True),
    ('1 and "x"', True),
    ('0 or ""', False),
    ('false and (1 / 0)', False),
    ('true or (1 / 0)', True),
    ('len(steps.validate.outputs.errors)', 1),
    ('len("abc")', 3),
    ('len({a: 1})', 1),
    ('len(null)', 0),
    ('lower("AbC")', "abc"),
    ('upper(null)', None),
    ('contains(steps.validate.outputs.errors, "bad currency")', True),
    ('contains("abc", "b")', True),
    ('startswith(steps.extract.outputs.invoice_number, "INV-")', True),
    ('startswith(null, "x")', False),
    ('join(["a", "b"])', "a,b"),
    ('join(["a", "b"], " ")', "a b"),
    ('join(null, "-")', ""),
    ('split("a b  c")', ["a", "b", "c"]),
    ('split("a,b", ",")', ["a", "b"]),
    ('split(null, ",")', []),
    ('now()', "2026-09-22T21:50:03Z"),
    ('steps.extract.outputs.items[0].sku', "A"),
    ('steps.extract.outputs.items[5]', None),
    ('steps.extract.outputs.items[-1].qty', 2),
    ('{a: 1, "b c": [true, null]}', {"a": 1, "b c": [True, None]}),
    ('[1, 2, 3,]', [1, 2, 3]),
    ('1.5e2', 150.0),
]

@pytest.mark.parametrize("src,expected", OK)
def test_ok(src, expected):
    got = evaluate(src, scope())
    assert got == expected and type(got) is type(expected), (got, expected)

ERR = [
    ('steps.validate.outputs.fields.total > null', "cannot compare number > null"),
    ('steps.save.outputs.total > 3', "cannot compare null > number"),
    ('"a" < 1', "cannot compare string < number"),
    ('true < false', "cannot compare boolean < boolean"),
    ('1 + "a"', "cannot add number and string"),
    ('1 + null', "cannot add number and null"),
    ('1 / 0', "division by zero"),
    ('1 % 0', "division by zero"),
    ('"a" * 2', "needs numbers"),
    ('-"a"', "cannot negate string"),
    ('unlabelled', "unknown name 'unlabelled'"),
    ('frobnicate(1)', "unknown function 'frobnicate'"),
    ('len(1, 2)', "len() takes 1 argument(s), got 2"),
    ('now(1)', "now() takes 0 argument(s), got 1"),
    ('coalesce()', "coalesce() takes 1..99 argument(s), got 0"),
    ('len(5)', "len() of number"),
    ('lower(1)', "lower() needs a string"),
    ('join([1, 2])', "join() needs a list of strings"),
    ('split("a", "")', "separator must be a non-empty string"),
    ('edges["nope.done"][0].taken', "unknown edge 'nope.done'"),
    ('edges["validate.done"][7].taken', "has no branch 7"),
    ('edges["validate.done"].bogus.taken', "has no branch named 'bogus'"),
    ('edges["validate.done"][0].count', "only have .taken"),
    ('steps.read.outputs.text.foo', "cannot access 'foo' on a string"),
    ('steps.extract.outputs.items["x"]', "list index must be an integer"),
    ('1 in 2', "'in' needs a string, list or object"),
    ('1 in "abc"', "'in' a string needs a string"),
]

@pytest.mark.parametrize("src,msg", ERR)
def test_err(src, msg):
    with pytest.raises(EvalError) as e:
        evaluate(src, scope())
    assert msg in str(e.value), str(e.value)

SYNTAX = [
    ("a < b < c", "1:7"), ("if a then b", "1:12"), ("1 +", "1:4"), ("a ==", "1:5"),
    ("unlabelled here", "1:12"), ("x + if a then 1 else 2", "1:8"), ("f(a)(b)", "1:5"),
    ('"unterminated', "1:1"), ("a &&& b", "1:3"), ('"\\q"', "1:1"), ("", "1:1"),
]

MSG = [
    ("if a then b elze c", "1:13: unexpected 'elze'; expected an operator, 'elif' or 'else'"),
    ("unlabelled here", "1:12: unexpected 'here'; expected an operator or the end of the expression; if you meant literal text"),
    ("if a then b", "1:12: unexpected end of expression; expected 'elif' or 'else'"),
    ("{a 1}", "1:4: unexpected '1'; expected ':'"),
    ("[1 2]", "1:4: unexpected '2'; expected an operator, ']' or ','"),
    ("steps.x.", "1:9: unexpected end of expression; expected a name"),
    ("1 +", "1:4: unexpected end of expression; expected a value"),
    ("a < b < c", "1:7: unexpected '<'; expected an operator or the end of the expression"),
]

@pytest.mark.parametrize("src,msg", MSG)
def test_syntax_msg(src, msg):
    with pytest.raises(ExprSyntaxError) as e:
        parse(src)
    assert str(e.value).startswith(msg), str(e.value)

@pytest.mark.parametrize("src,pos", SYNTAX)
def test_syntax(src, pos):
    with pytest.raises(ExprSyntaxError) as e:
        parse(src)
    assert str(e.value).startswith(pos), str(e.value)

def test_refs():
    rs = references('if steps.validate.outputs.fields.total > 10000 and edges["validate.done"].retry.taken < 2 then env.REVIEW_DIR else default(steps.fix.outputs.x, run.id) + steps.extract.outputs.items[steps.fix.runs].sku')
    got = [(str(r), r.guarded) for r in rs]
    assert got == [
        ("steps.validate.outputs.fields.total", False),
        ("edges.validate.done.retry.taken", False),
        ("env.REVIEW_DIR", False),
        ("steps.fix.outputs.x", True),
        ("run.id", False),
        ("steps.extract.outputs.items[*].sku", False),
        ("steps.fix.runs", False),
    ], got

def test_with_tree():
    v = evaluate_value({"record": "steps.validate.outputs.fields", "n": 3, "flag": True, "none": None,
                        "nested": {"d": "env.RECORDS_DIR", "l": ["run.id", 1.5]}}, scope())
    assert v == {"record": {"total": 12000}, "n": 3, "flag": True, "none": None,
                 "nested": {"d": "/records", "l": ["run-123", 1.5]}}

def test_volatile_recorded():
    s = scope(); evaluate('run.id + now()', s)
    assert s.volatile == ["run-123", "2026-09-22T21:50:03Z"]

def test_keyword_member_names():
    s = scope(); s.steps["x"] = StepState(1, "done", {"in": 1, "if": 2})
    assert evaluate("steps.x.outputs.in + steps.x.outputs.if", s) == 3

def test_truthy():
    assert [truthy(v) for v in (None, False, 0, 0.0, "", [], {}, True, 1, -1, "0", [0], {"a": None})] == [False]*7 + [True]*6

def test_perf():
    import time
    s = scope(); src = 'steps.validate.outputs.fixable and steps.fix.runs < 3'
    parse(src); t = time.perf_counter()
    for _ in range(10000): evaluate(src, s)
    per = (time.perf_counter() - t) / 10000
    print(f"eval per call {per*1e6:.1f}us")
    parse.cache_clear(); t = time.perf_counter()
    for i in range(200): parse(src + f" or {i} > 1")
    print(f"parse per call {(time.perf_counter()-t)/200*1e6:.1f}us")

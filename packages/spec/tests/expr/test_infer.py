"""M3 type inference and assignability ($DRAFTS/01 §7.10, §12.5; PLAN §4.1 infer row).

The TypeEnv fixtures (`site_env`, `exit_aware_env`, `error_edge_env`) come from this directory's conftest: the dogfood
process as the validator sees it at `validate.done[0].with.dest`.
"""

import pytest

from wynd.spec.expr.analysis import StepView, TypeEnv
from wynd.spec.expr.infer import check_assignable, infer_type
from wynd.spec.interface import interface_from_fields
from wynd.spec.records import ProcessError
from wynd.spec.typelang import parse_type

NULL = {"type": "null"}
STRING = {"type": "string"}
INTEGER = {"type": "integer"}
NUMBER = {"type": "number"}
BOOLEAN = {"type": "boolean"}
OBJECT = {"type": "object"}
DATE = {"type": "string", "format": "date"}
DATETIME = {"type": "string", "format": "date-time"}
PATH = {"type": "string", "format": "path"}


def nullable(schema: dict) -> dict:
    return {"anyOf": [schema, NULL]}


def closed(required: dict, optional: dict | None = None) -> dict:
    schema = {"type": "object", "properties": {**required, **(optional or {})}, "additionalProperties": False}
    if required:
        schema["required"] = sorted(required)
    return schema


def array(items: dict) -> dict:
    return {"type": "array", "items": items}


def infer(expr: str, env: TypeEnv) -> dict:
    """The inferred schema of an expression that must produce no findings."""
    schema, diagnostics = infer_type(expr, env)
    assert diagnostics == []
    return schema


EMPTY = TypeEnv(steps={}, edges={}, process_inputs={})


# --------------------------------------------------------------------------------------------------- infer_type

# $DRAFTS/01 §12.5, at the site validate.done[0].with.dest (REVIEW_DIR and RECORDS_DIR are declared, A and X not).
DRAFT_TABLE = [
    ("steps.fix.runs < 3", BOOLEAN),
    ('if c then env.A else "x"', nullable(STRING)),
    ("steps.extract.outputs.total * 2", NUMBER),
    ("len(steps.validate.outputs.errors)", INTEGER),
    ('default(env.X, "d")', STRING),
    ("steps.validate.outputs.fields", OBJECT),
]


@pytest.mark.parametrize("text,expected", DRAFT_TABLE)
def test_infer_draft_table(site_env, text, expected):
    assert infer(text, site_env) == expected


def test_infer_draft_table_type_error(site_env):
    schema, [diagnostic] = infer_type("steps.extract.outputs.invoice_number + 1", site_env)
    assert schema == {}
    assert (diagnostic.severity, diagnostic.code) == ("error", "E-TYPE-OP")
    assert diagnostic.message == "cannot add string and integer"


def test_outputs_of_a_step_with_two_possible_exits():
    view = StepView({
        "done": {"type": "object", "properties": {"exit": {"const": "done"}, "a": STRING}, "required": ["exit", "a"]},
        "other": {
            "type": "object", "properties": {"exit": {"const": "other"}, "a": INTEGER}, "required": ["exit", "a"]
        },
    })
    env = TypeEnv(steps={"x": view}, edges={}, process_inputs={})
    assert infer("steps.x.outputs", env) == {
        "anyOf": [
            {"type": "object", "properties": {"a": STRING}, "required": ["a"]},
            {"type": "object", "properties": {"a": INTEGER}, "required": ["a"]},
        ]
    }
    assert infer("steps.x.outputs.a", env) == {"anyOf": [STRING, INTEGER]}
    assert infer("steps.x.exit", env) == {"enum": ["done", "other"]}


LITERALS = [
    ("1", INTEGER),
    ("1.5", NUMBER),
    ("1e3", NUMBER),
    ('"a"', STRING),
    ("true", BOOLEAN),
    ("null", NULL),
    ("[]", {"type": "array"}),
    ("[1, 2]", array(INTEGER)),
    ('[1, 2.5, "a", 3]', array({"anyOf": [INTEGER, NUMBER, STRING]})),
    ('[null, "a"]', array(nullable(STRING))),
    ("{}", closed({})),
    ('{b: 1, "a c": [true]}', closed({"b": INTEGER, "a c": array(BOOLEAN)})),
]


@pytest.mark.parametrize("text,expected", LITERALS)
def test_literals_get_their_json_type(text, expected):
    assert infer(text, EMPTY) == expected


REFERENCES = [
    ("run.id", STRING),
    ("env.RECORDS_DIR", STRING),  # declared in process.env.vars: the manifest makes it required
    ('env["REVIEW_DIR"]', STRING),
    ("env.MISSING", nullable(STRING)),
    ("steps.validate.runs", INTEGER),
    ("steps.save.runs", INTEGER),
    ('edges["validate.done"][0].taken', INTEGER),
    ('edges["validate.done"].retry.taken', INTEGER),
    ("steps.validate.exit", {"enum": ["done"]}),
    ("steps.fix.exit", {"anyOf": [{"enum": ["done"]}, NULL]}),  # fix may not have run
    ("steps.save.exit", NULL),  # save never ran on any path to the site
    ("steps.save.outputs", NULL),
    ("steps.save.outputs.record", NULL),
    ("steps.read.outputs.text", STRING),
    ("steps.read.outputs.pages", INTEGER),
    ("steps.extract.outputs.due_date", DATE),
    ("steps.fix.outputs.total", nullable(NUMBER)),
    ("steps.validate.outputs.record", nullable(OBJECT)),
    ("steps.validate.outputs.errors", array(STRING)),
    ("steps.validate.outputs.errors[0]", STRING),
    ("steps.validate.outputs.errors[steps.fix.runs]", STRING),
    ("steps.validate.outputs.fields.total", {}),  # `fields` is an open object: the rest is unknown
    ("process.inputs.pdf_path", PATH),
    ("previous.outputs.valid", BOOLEAN),
    ("previous.summary.step", STRING),
    ("previous.summary.note", {"type": "string", "default": ""}),
    ("previous.summary.key_outputs.total", {}),
]


@pytest.mark.parametrize("text,expected", REFERENCES)
def test_reference_types(site_env, text, expected):
    assert infer(text, site_env) == expected


def test_whole_objects(site_env):
    assert infer("steps.extract.outputs", site_env) == closed({
        "invoice_number": STRING, "total": NUMBER, "currency": STRING, "due_date": DATE,
    })
    assert infer("steps.fix.outputs", site_env) == nullable(infer("steps.extract.outputs", site_env))
    assert infer("process.inputs", site_env) == closed({"pdf_path": PATH})
    summary = infer("previous.summary", site_env)
    assert summary["type"] == "object"
    assert list(summary["properties"]) == ["step", "exit", "key_outputs", "note"]
    assert infer("steps.fix", site_env) == closed({
        "runs": INTEGER,
        "exit": {"anyOf": [{"enum": ["done"]}, NULL]},
        "outputs": nullable(infer("steps.extract.outputs", site_env)),
    })


@pytest.mark.parametrize(
    "text",
    [
        "steps.nope.outputs.x",  # E-REF-STEP
        "steps.extract.outputs.totl",  # E-REF-FIELD
        "steps.extract.outputs.exit",  # scope outputs never carry the exit
        "steps.x.foo",  # E-REF-SHAPE
        "process.inputs.pdf",  # E-REF-INPUT
        "process.goal",
        "previous.summary.nope",
        "previous.exit",
        "run.idx",
        "env",
        "env.A.b",
        "edges",
        'edges["validate.done"][0]',
        "unlabelled",  # E-REF-NAME
        "lenn(1)",  # E-EXPR-FUNC
        "len(1, 2)",  # E-EXPR-ARITY
        "a <",  # E-EXPR-SYNTAX
    ],
)
def test_invalid_references_and_expressions_are_unknown_and_not_reported_again(site_env, text):
    assert infer_type(text, site_env) == ({}, [])


def test_previous_is_unknown_when_the_site_has_no_previous_step(site_env):
    env = TypeEnv(steps=site_env.steps, edges=site_env.edges, process_inputs=site_env.process_inputs)
    assert infer("previous.outputs.valid", env) == {}
    assert infer("previous.summary.step", env) == {}


def test_exit_aware_outputs(exit_aware_env):
    # `record` is not declared on `invalid`, so it reads as null when validate took that exit.
    assert infer("steps.validate.outputs.record", exit_aware_env) == nullable(OBJECT)
    assert infer("steps.validate.outputs.valid", exit_aware_env) == BOOLEAN
    assert infer("steps.validate.exit", exit_aware_env) == {"enum": ["done", "invalid"]}
    assert infer("coalesce(steps.validate.outputs.record, {})", exit_aware_env) == {
        "anyOf": [OBJECT, closed({})]
    }


def test_error_exit_outputs_are_the_step_error_fields(error_edge_env):
    assert infer("steps.read.outputs.message", error_edge_env) == STRING
    assert infer("steps.read.outputs.attempts", error_edge_env) == {"type": "integer", "default": 1}
    cause = infer("steps.read.outputs.cause", error_edge_env)
    assert cause["type"] == "string" and "worker_crash" in cause["enum"]
    child = infer("steps.read.outputs.child", error_edge_env)
    assert child["anyOf"][-1] == NULL
    assert infer("steps.read.outputs.child.cause", error_edge_env)["anyOf"][-1] == NULL
    # the recursive StepError <-> ProcessError reference is unknown rather than an endless walk
    assert infer("steps.read.outputs.child.step_error.child", error_edge_env) == {}


def test_member_access_through_a_nullable_object_is_nullable():
    meta = nullable(closed({"a": STRING, "b": closed({"c": INTEGER})}))
    done = {"type": "object", "properties": {"exit": {"const": "done"}, "meta": meta}, "required": ["exit", "meta"]}
    env = TypeEnv(steps={"s": StepView({"done": done})}, edges={}, process_inputs={})
    assert infer("steps.s.outputs.meta", env) == meta
    assert infer("steps.s.outputs.meta.a", env) == nullable(STRING)
    assert infer("steps.s.outputs.meta.b.c", env) == nullable(INTEGER)


def test_pydantic_schemas_with_refs_are_normalised():
    # interface_from_models output: nested models live in $defs and are referenced.
    done = {
        "type": "object",
        "title": "Done",
        "properties": {
            "exit": {"const": "done", "default": "done", "type": "string"}, "item": {"$ref": "#/$defs/Item"},
        },
        "$defs": {"Item": {"type": "object", "title": "Item", "properties": {"sku": STRING}, "required": ["sku"]}},
    }
    env = TypeEnv(steps={"s": StepView({"done": done})}, edges={}, process_inputs={})
    assert infer("steps.s.outputs", env) == {
        "type": "object",
        "properties": {"item": {"type": "object", "properties": {"sku": STRING}, "required": ["sku"]}},
    }
    assert infer("steps.s.outputs.item.sku", env) == STRING


def test_non_reference_member_and_index_access(site_env):
    assert infer("{a: 1, b: [true]}.a", site_env) == INTEGER
    assert infer('{a: 1}["a"]', site_env) == INTEGER
    assert infer("{a: 1}.b", site_env) == {}
    assert infer("[1, 2][0]", site_env) == INTEGER
    assert infer("(if steps.validate.outputs.valid then steps.fix.outputs else null).total", site_env) == nullable(
        NUMBER
    )


OPERATORS = [
    ("1 + 2", INTEGER),
    ("1 + 2.5", NUMBER),
    ('"a" + "b"', STRING),
    ('steps.extract.outputs.due_date + "x"', STRING),
    ('[1] + ["a"]', array({"anyOf": [INTEGER, STRING]})),
    ("[1] + []", {"type": "array"}),
    ("steps.read.outputs.pages - 1", INTEGER),
    ("2 * steps.read.outputs.pages", INTEGER),
    ("7 % 3", INTEGER),
    ("7 % 2.5", NUMBER),
    ("7 / 7", NUMBER),
    ("-1", INTEGER),
    ("-steps.extract.outputs.total", NUMBER),
    ("steps.validate.outputs.fields.total * 2", NUMBER),  # unknown operand: arithmetic still yields a number
    ("steps.validate.outputs.fields.total + 1", {}),
    ("-steps.validate.outputs.fields.total", {}),
    ("not 1", BOOLEAN),
    ('1 and "x"', BOOLEAN),
    ("null or []", BOOLEAN),
    ('1 == "a"', BOOLEAN),
    ("null != steps.fix.outputs", BOOLEAN),
    ('"a" < "b"', BOOLEAN),
    ('"GBP" in ["GBP", "EUR"]', BOOLEAN),
    ('"x" not in steps.read.outputs.text', BOOLEAN),
    ('"total" in steps.validate.outputs.fields', BOOLEAN),
    ('"a" in null', BOOLEAN),
    ('if true then 1 elif false then "a" else null', {"anyOf": [INTEGER, STRING, NULL]}),
    ("if true then 1 else 2", INTEGER),
    ("if true then 1 else steps.validate.outputs.fields.total", {}),
    # partially valid unions are not definite errors; the result covers the valid combinations only
    ('(if true then 1 else "a") + 1', INTEGER),
]


@pytest.mark.parametrize("text,expected", OPERATORS)
def test_operator_types(site_env, text, expected):
    assert infer(text, site_env) == expected


BUILTIN_RESULTS = [
    ('len("abc")', INTEGER),
    ("len(null)", INTEGER),
    ("len(steps.validate.outputs.fields)", INTEGER),
    ('lower("A")', STRING),
    ("upper(env.MISSING)", nullable(STRING)),
    ("lower(steps.validate.outputs.fields.x)", STRING),
    ('contains(steps.validate.outputs.errors, "bad")', BOOLEAN),
    ('startswith(steps.extract.outputs.invoice_number, "INV-")', BOOLEAN),
    ('startswith(null, "x")', BOOLEAN),
    ('join(["a", "b"])', STRING),
    ('join(steps.validate.outputs.errors, " ")', STRING),
    ('split("a b")', array(STRING)),
    ('split(env.MISSING, ",")', array(STRING)),
    ("now()", DATETIME),
    ('default(steps.extract.outputs.currency, "GBP")', STRING),
    ("default(steps.fix.outputs.total, 0)", {"anyOf": [NUMBER, INTEGER]}),
    ('default(0, "x")', INTEGER),  # the fallback is never used for a value that cannot be null
    ('default(null, "x")', STRING),
    ("default(steps.save.outputs, {})", closed({})),
    ("coalesce(null)", NULL),
    ("coalesce(null, env.MISSING)", nullable(STRING)),
    ('coalesce(null, env.MISSING, "x", 1)', STRING),
    ("coalesce(steps.fix.outputs.total, steps.extract.outputs.total)", NUMBER),
    ("coalesce(steps.validate.outputs.fields.x, 1)", {}),
]


@pytest.mark.parametrize("text,expected", BUILTIN_RESULTS)
def test_builtin_types(site_env, text, expected):
    assert infer(text, site_env) == expected


TYPE_ERRORS = [
    ('1 + "a"', "cannot add integer and string"),
    ("true + 1", "cannot add boolean and integer"),
    ('[1] + "a"', "cannot add list[integer] and string"),
    ("null + 1", "cannot add null and integer"),
    ("true < false", "cannot compare boolean < boolean"),
    ('"a" < 1', "cannot compare string < integer"),
    ("steps.extract.outputs.total > null", "cannot compare number > null"),
    ('"a" / "b"', "operator '/' needs numbers, got string and string"),
    ("[1] - 1", "operator '-' needs numbers, got list[integer] and integer"),
    ('"a" % 2', "operator '%' needs numbers, got string and integer"),
    ('-"a"', "cannot negate string"),
    ("-null", "cannot negate null"),
    ("1 in 2", "'in' needs a string, list or object on the right, got integer"),
    ('1 in "abc"', "'in' a string needs a string, got integer"),
    ('1 not in "abc"', "'in' a string needs a string, got integer"),
    ("len(5)", "len() of integer"),
    ("len(steps.extract.outputs.total)", "len() of number"),
    ("lower(1)", "lower() needs a string, got integer"),
    ("contains(1, 2)", "'in' needs a string, list or object on the right, got integer"),
    ('contains("abc", 1)', "'in' a string needs a string, got integer"),
    ('startswith(1, "a")', "startswith() needs strings, got integer and string"),
    ("startswith(steps.read.outputs.text, 1)", "startswith() needs strings, got string and integer"),
    ("join([1, 2])", "join() needs a list of strings and a string separator"),
    ('join(["a"], 1)', "join() needs a list of strings and a string separator"),
    ("join(1)", "join() needs a list of strings and a string separator"),
    ("split(1)", "split() needs a string, got integer"),
    ('split("a", 1)', "split() separator must be a non-empty string"),
]


@pytest.mark.parametrize("text,message", TYPE_ERRORS)
def test_definite_type_errors(site_env, text, message):
    _, diagnostics = infer_type(text, site_env)
    assert [(d.severity, d.code, d.message) for d in diagnostics] == [("error", "E-TYPE-OP", message)]


@pytest.mark.parametrize(
    "text",
    [
        'steps.validate.outputs.fields.total + "x"',  # an unknown operand is never a definite error
        "steps.validate.outputs.fields.total > true",
        "len(steps.validate.outputs.fields.total)",
        '(if true then 1 else "a") < 2',  # some combinations are valid
        '1 in (if true then "a" else [1])',
        '"a" in steps.validate.outputs.fields',
        "1 in {a: 1}",
        '"a" == 1',
        "not 5",
        "if 3 then 1 else 2",  # truthiness: conditions may have any type
    ],
)
def test_no_finding_unless_every_combination_fails(site_env, text):
    assert infer_type(text, site_env)[1] == []


NULLABLE_OPERANDS = [
    ("steps.fix.outputs.total > 1", "steps.fix.outputs.total", ">"),
    ("1 <= steps.fix.outputs.total", "steps.fix.outputs.total", "<="),
    ("steps.fix.outputs.total * 2", "steps.fix.outputs.total", "*"),
    ("steps.fix.outputs.total / 2", "steps.fix.outputs.total", "/"),
    ('env.MISSING + "/x"', "env.MISSING", "+"),
    ("-steps.fix.outputs.total", "steps.fix.outputs.total", "-"),
    ("steps.validate.outputs.fields.total > null", "null", ">"),  # unknown other side: not definite
]


@pytest.mark.parametrize("text,operand,op", NULLABLE_OPERANDS)
def test_nullable_operand_of_ordering_or_arithmetic_warns(site_env, text, operand, op):
    schema, [diagnostic] = infer_type(text, site_env)
    assert (diagnostic.severity, diagnostic.code) == ("warning", "W-TYPE-NULL")
    assert diagnostic.message == f"{operand} may be null, and '{op}' fails on null; guard it with default(...)"
    start = text.index(operand)
    assert diagnostic.span == (start, start + len(operand))
    assert (diagnostic.line, diagnostic.column) == (1, start + 1)


@pytest.mark.parametrize(
    "text",
    [
        "default(steps.fix.outputs.total, 0) > 1",
        "coalesce(steps.fix.outputs.total, 1) * 2",
        "steps.fix.outputs.total == 1",
        '"x" in env.MISSING',
        "len(env.MISSING)",
        "lower(env.MISSING)",
        'startswith(env.MISSING, "a")',
        'join(env.MISSING, "-")',
        "split(env.MISSING)",
        "steps.fix.outputs.total and 1",
        "if steps.fix.outputs.total then 1 else 0",
    ],
)
def test_null_tolerant_operations_do_not_warn(site_env, text):
    assert infer_type(text, site_env)[1] == []


def test_errors_do_not_cascade(site_env):
    schema, diagnostics = infer_type('(1 + "a") * 2 > 3', site_env)
    assert schema == BOOLEAN
    assert [d.code for d in diagnostics] == ["E-TYPE-OP"]
    assert infer_type('-(1 + "a")', site_env)[0] == {}


def test_findings_are_located_in_the_expression_and_in_source_order(site_env):
    text = 'steps.fix.outputs.total > 1 and\n  len(5) == 1 or "a" / 2 > 0'
    _, diagnostics = infer_type(text, site_env)
    assert [(d.code, d.line, d.column, d.span) for d in diagnostics] == [
        ("W-TYPE-NULL", 1, 1, (0, 23)),
        ("E-TYPE-OP", 2, 3, (34, 40)),
        ("E-TYPE-OP", 2, 18, (49, 56)),
    ]
    assert all(d.file is None and d.loc == () for d in diagnostics)


def test_findings_inside_subscripts_conditions_and_arguments(site_env):
    cases = [
        'steps.validate.outputs.errors[1 + "a"]',
        '[1, 2][true < 1]',
        'if true < 1 then 1 else 2',
        'default(-"a", 1)',
        '{a: -"a"}',
        '[-"a"]',
        'not -"a"',
        'len(-"a", 1)',  # arity errors still check their arguments
    ]
    for text in cases:
        assert [d.code for d in infer_type(text, site_env)[1]] == ["E-TYPE-OP"], text


def test_results_are_independent_copies(site_env):
    first = infer("run.id", site_env)
    first["format"] = "path"
    assert infer("run.id", site_env) == STRING
    assert infer('"a"', site_env) == STRING


# --------------------------------------------------------------------------------------------------- check_assignable

def closed_draft(*names: str, required: tuple[str, ...] = ()) -> dict:
    return {
        "type": "object",
        "properties": {name: STRING for name in names},
        "required": list(required),
        "additionalProperties": False,
    }


# $DRAFTS/01 §12.5 check_assignable cases.
ASSIGNABLE_DRAFT = [
    (INTEGER, NUMBER, "ok"),
    (NUMBER, INTEGER, "warning"),
    (nullable(STRING), STRING, "warning"),
    (STRING, DATE, "warning"),
    (DATE, STRING, "ok"),
    (PATH, STRING, "ok"),
    (STRING, PATH, "ok"),
    (BOOLEAN, STRING, "error"),
    ({}, STRING, "ok"),
    ({}, closed_draft("a", required=("a",)), "ok"),
    (OBJECT, closed_draft("a", required=("a",)), "ok"),
    (closed_draft("a", required=("a",)), closed_draft("a", "b", required=("a", "b")), "error"),
    (array(INTEGER), array(NUMBER), "ok"),
    (array(STRING), array(INTEGER), "error"),
    ({"anyOf": [INTEGER, STRING]}, INTEGER, "warning"),
]


@pytest.mark.parametrize("src,dst,level", ASSIGNABLE_DRAFT)
def test_check_assignable_draft_table(src, dst, level):
    assert check_assignable(src, dst)[0] == level


ASSIGNABLE = [
    # ANY on either side
    (STRING, {}, ("ok", "")),
    ({"$ref": "#/$defs/Gone"}, STRING, ("ok", "")),
    ({"default": 3}, INTEGER, ("ok", "")),
    # scalars and formats
    (INTEGER, INTEGER, ("ok", "")),
    (BOOLEAN, BOOLEAN, ("ok", "")),
    (NUMBER, INTEGER, ("warning", "expected integer got number")),
    (STRING, DATE, ("warning", "expected date got string (parsed at run time)")),
    (STRING, DATETIME, ("warning", "expected datetime got string (parsed at run time)")),
    (DATE, DATETIME, ("warning", "expected datetime got date (parsed at run time)")),
    (DATETIME, DATETIME, ("ok", "")),
    (DATE, PATH, ("ok", "")),
    (BOOLEAN, STRING, ("error", "expected string got boolean")),
    (STRING, NUMBER, ("error", "expected number got string")),
    (array(STRING), STRING, ("error", "expected string got list[string]")),
    (OBJECT, array(STRING), ("error", "expected list[string] got object")),
    # null
    (nullable(STRING), STRING, ("warning", "may be null")),
    (nullable(STRING), nullable(STRING), ("ok", "")),
    (NULL, nullable(INTEGER), ("ok", "")),
    (NULL, INTEGER, ("error", "expected integer got null")),
    (nullable(NUMBER), INTEGER, ("warning", "expected integer got number; may be null")),
    (nullable(BOOLEAN), INTEGER, ("error", "expected integer got boolean")),
    ({"type": ["string", "null"]}, STRING, ("warning", "may be null")),
    ({"anyOf": [STRING, NULL], "default": None}, STRING, ("warning", "may be null")),
    # unions
    ({"anyOf": [INTEGER, NUMBER]}, NUMBER, ("ok", "")),
    ({"anyOf": [INTEGER, NUMBER]}, INTEGER, ("warning", "expected integer but may get number")),
    ({"anyOf": [INTEGER, STRING]}, INTEGER, ("warning", "expected integer but may get string")),
    ({"anyOf": [BOOLEAN, OBJECT]}, INTEGER, ("error", "expected integer got boolean | object")),
    (INTEGER, {"anyOf": [STRING, INTEGER]}, ("ok", "")),
    (NUMBER, {"anyOf": [STRING, INTEGER]}, ("warning", "expected integer got number")),
    (BOOLEAN, {"anyOf": [STRING, INTEGER]}, ("error", "expected string | integer got boolean")),
    # enums and consts
    ({"enum": ["done", "invalid"]}, {"enum": ["done", "invalid", "x"]}, ("ok", "")),
    ({"enum": ["done", "invalid"]}, {"enum": ["done"]}, ("warning", 'expected "done" but may get "invalid"')),
    ({"enum": ["done", "invalid"]}, {"const": "x"}, ("error", 'expected "x" got "done" | "invalid"')),
    ({"enum": ["done"]}, STRING, ("ok", "")),
    (STRING, {"enum": ["high", "normal"]}, ("ok", "")),  # a plain string's values are unknown
    ({"enum": [1, 2]}, STRING, ("error", "expected string got 1 | 2")),
    # nested objects and arrays: the message names the path
    (
        closed({"fields": closed({"total": STRING})}),
        closed({"fields": closed({"total": NUMBER})}),
        ("error", "fields.total: expected number got string"),
    ),
    (
        closed({"errors": array(closed({"code": INTEGER}))}),
        closed({"errors": array(closed({"code": STRING}))}),
        ("error", "errors[].code: expected string got integer"),
    ),
    (array(STRING), array(INTEGER), ("error", "items: expected integer got string")),
    (closed({"a": STRING}), closed({"a": STRING, "b": STRING, "c": STRING}), ("error", "missing b, c")),
    (closed({"a": STRING}), closed({"a": STRING}, {"b": STRING}), ("ok", "")),  # b is optional in dst
    (closed({"a": nullable(STRING)}), closed({"a": STRING}), ("warning", "a: may be null")),
    (
        closed({"a": nullable(STRING), "b": NUMBER}),
        closed({"a": STRING, "b": INTEGER}),
        ("warning", "a: may be null; b: expected integer got number"),
    ),
    (closed({"a": STRING, "extra": INTEGER}), closed({"a": STRING}), ("ok", "")),  # bindings select by name
    (closed({}), closed({}), ("ok", "")),
    (closed({}), closed({"a": STRING}), ("error", "missing a")),
    (closed({"a": STRING}), OBJECT, ("ok", "")),
    ({"type": "object", "additionalProperties": True}, closed({"a": STRING}), ("ok", "")),
    (array(nullable(STRING)), array(STRING), ("warning", "items: may be null")),
    ({"type": "array"}, array(STRING), ("ok", "")),
]


@pytest.mark.parametrize("src,dst,expected", ASSIGNABLE)
def test_check_assignable(src, dst, expected):
    assert check_assignable(src, dst) == expected


def test_process_error_into_an_on_error_handler_input():
    # on_error: the handler Input is bound by field name from the ProcessError JSON (pydantic schema with $defs).
    process_error = ProcessError.model_json_schema()
    assert "$defs" in process_error
    handler = closed({"message": STRING, "cause": STRING, "run_id": STRING}, {"step": nullable(STRING)})
    assert check_assignable(process_error, handler) == ("ok", "")
    edge = closed({"message": STRING, "edge": STRING})
    assert check_assignable(process_error, edge) == ("warning", "edge: may be null")
    assert check_assignable(process_error, closed({"message": STRING, "nope": STRING})) == ("error", "missing nope")
    cause = closed({"cause": {"enum": ["timeout", "max_traversals"]}})
    assert check_assignable(process_error, cause)[0] == "warning"
    assert check_assignable(process_error, closed({"cause": {"enum": ["later"]}}))[0] == "error"
    step_error = closed({"step_error": closed({"message": STRING, "attempts": INTEGER})})
    assert check_assignable(process_error, step_error) == ("warning", "step_error: may be null")


# --------------------------------------------------------------------------------------------------- the dogfood

INVOICE = {
    "supplier": "string", "invoice_number": "string", "total": "number", "currency": "string", "due_date": "date",
}
ERRORS = [{"field": "string", "code": "string", "message": "string", "fixable": "boolean"}]
RECORD = {"key": "string", **INVOICE}
# The six proto interfaces of examples/invoices (inputs, outputs by exit).
PROTOS = {
    "read": ({"pdf_path": "path"}, {"done": {"text": "string", "pages": "integer"}}),
    "extract": ({"invoice_text": "string"}, {"done": INVOICE, "not_an_invoice": {}}),
    "validate": (
        {"fields": INVOICE},
        {"done": {"valid": "boolean", "fixable": "boolean", "errors": ERRORS, "fields": INVOICE, "record": RECORD}},
    ),
    "fix": ({"invoice_text": "string", "fields": INVOICE, "errors": ERRORS}, {"done": INVOICE}),
    "save": ({"record": RECORD, "dest": "path"}, {"done": {"record": RECORD, "path": "path"}}),
    "escalate": (
        {"fields": INVOICE, "errors": ERRORS, "queue_dir": "path", "run_id": "string"},
        {"done": {"ticket_path": "path"}},
    ),
}


def interface(key: str):
    inputs, outputs = PROTOS[key]
    return interface_from_fields(
        {name: parse_type(t) for name, t in inputs.items()},
        {exit: {name: parse_type(t) for name, t in fields.items()} for exit, fields in outputs.items()},
    )


def dogfood_site(ran: dict[str, tuple[str, ...]], maybe: tuple[str, ...] = (), previous: str | None = None) -> TypeEnv:
    """TypeEnv with `ran` steps definitely completed on the given exits and `maybe` steps possibly unrun."""
    steps = {}
    for key in PROTOS:
        exits = ran.get(key, ("done",) if key in maybe else ())
        schemas = {exit: interface(key).outputs[exit] for exit in exits}
        steps[key] = StepView(schemas, may_be_unrun=key not in ran)
    return TypeEnv(
        steps=steps,
        edges={"validate.done": [None, None, None], "fix.done": [None]},
        process_inputs=interface("read").input,
        previous=steps[previous] if previous else None,
        env_declared=frozenset({"RECORDS_DIR", "REVIEW_DIR", "ESCALATIONS_DIR"}),
    )


def input_field(key: str, field: str) -> dict:
    return interface(key).input["properties"][field]


AT_VALIDATE_DONE = {"ran": {"read": ("done",), "extract": ("done",), "validate": ("done",)}, "maybe": ("fix",)}
# (site, expression, target step or "$exit.done", field) for every binding of the dogfood process.yaml.
DOGFOOD_BINDINGS = [
    ({"ran": {"read": ("done",)}}, "steps.read.outputs.text", "extract", "invoice_text"),
    ({"ran": {"read": ("done",), "extract": ("done",)}}, "steps.extract.outputs", "validate", "fields"),
    (AT_VALIDATE_DONE, "steps.validate.outputs.record", "save", "record"),
    (
        AT_VALIDATE_DONE,
        "if steps.validate.outputs.fields.total > 10000 then env.REVIEW_DIR\nelse env.RECORDS_DIR",
        "save",
        "dest",
    ),
    (AT_VALIDATE_DONE, "steps.read.outputs.text", "fix", "invoice_text"),
    (AT_VALIDATE_DONE, "steps.validate.outputs.fields", "fix", "fields"),
    (AT_VALIDATE_DONE, "steps.validate.outputs.errors", "fix", "errors"),
    (AT_VALIDATE_DONE, "steps.validate.outputs.fields", "escalate", "fields"),
    (AT_VALIDATE_DONE, "steps.validate.outputs.errors", "escalate", "errors"),
    (AT_VALIDATE_DONE, "env.ESCALATIONS_DIR", "escalate", "queue_dir"),
    (AT_VALIDATE_DONE, "run.id", "escalate", "run_id"),
    ({"ran": {"read": ("done",), "extract": ("done",), "validate": ("done",), "fix": ("done",)}},
     "steps.fix.outputs", "validate", "fields"),
    ({"ran": {k: ("done",) for k in ("read", "extract", "validate", "save")}, "maybe": ("fix",)},
     "steps.save.outputs.record", "$exit.done", "record"),
]


@pytest.mark.parametrize("site,expr,target,field", DOGFOOD_BINDINGS)
def test_every_dogfood_binding_is_assignable(site, expr, target, field):
    schema, diagnostics = infer_type(expr, dogfood_site(**site))
    assert diagnostics == []
    if target == "$exit.done":
        record = {"record": parse_type("object")}
        dst = interface_from_fields({}, {"done": record}).outputs["done"]["properties"][field]
    else:
        dst = input_field(target, field)
    assert check_assignable(schema, dst) == ("ok", "")


@pytest.mark.parametrize(
    "expr", ["steps.validate.outputs.valid", "steps.validate.outputs.fixable and steps.fix.runs < 3"]
)
def test_dogfood_conditions_are_boolean(expr):
    assert infer_type(expr, dogfood_site(**AT_VALIDATE_DONE)) == ({"type": "boolean"}, [])


def test_an_undeclared_env_var_may_be_null_where_a_path_is_required():
    env = dogfood_site(**AT_VALIDATE_DONE)
    env = TypeEnv(steps=env.steps, edges=env.edges, process_inputs=env.process_inputs)  # no process.env.vars
    schema = infer("if true then env.REVIEW_DIR else env.RECORDS_DIR", env)
    assert check_assignable(schema, input_field("save", "dest")) == ("warning", "may be null")


def test_dogfood_type_mistakes_are_caught():
    env = dogfood_site(**AT_VALIDATE_DONE)
    record = infer("steps.validate.outputs.fields", env)
    assert check_assignable(record, input_field("save", "record")) == ("error", "missing key")
    text = infer("steps.read.outputs.pages", env)
    assert check_assignable(text, input_field("fix", "invoice_text")) == ("error", "expected string got integer")
    fields = infer("steps.fix.outputs", env)  # fix may not have run at validate.done
    assert check_assignable(fields, input_field("validate", "fields")) == ("warning", "may be null")

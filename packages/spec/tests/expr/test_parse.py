"""Parsing: syntax-error positions and messages ($DRAFTS/01 §7.8, §12.3), AST shape and precedence (§7.2–§7.3),
tokens, node positions and spans."""

import ast
from pathlib import Path

import pytest

import wynd.spec.expr
from wynd.spec.expr.errors import ExprError, ExprSyntaxError
from wynd.spec.expr.evaluator import parse
from wynd.spec.expr.nodes import Binary, Call, Cond, Index, ListLit, Lit, Member, Name, ObjectLit, Unary

SYNTAX = [
    ("a < b < c", "1:7", "unexpected '<'; expected an operator or the end of the expression"),
    ("if a then b", "1:12", "unexpected end of expression; expected 'elif' or 'else'"),
    ("1 +", "1:4", "unexpected end of expression; expected a value"),
    ("a ==", "1:5", "unexpected end of expression; expected a value"),
    ("unlabelled here", "1:12", "unexpected 'here'; expected an operator or the end of the expression"),
    ("x + if a then 1 else 2", "1:8", "unexpected 'a'; expected an operator or the end of the expression"),
    ("f(a)(b)", "1:5", "unexpected '('; expected an operator or the end of the expression"),
    ('"unterminated', "1:1", "unexpected character '\"'"),
    ("a &&& b", "1:3", "unexpected character '&'"),
    ('"\\q"', "1:1", "invalid escape \\q"),
    ("", "1:1", "unexpected end of expression; expected a value"),
    ("(a", "1:3", "unexpected end of expression; expected ')'"),
    ("steps.x.", "1:9", "unexpected end of expression; expected a name"),
    ("if a then b elze c", "1:13", "unexpected 'elze'; expected an operator, 'elif' or 'else'"),
    ("{a 1}", "1:4", "unexpected '1'; expected ':'"),
    ("[1 2]", "1:4", "unexpected '2'; expected an operator, ']' or ','"),
    ("if a b", "1:6", "unexpected 'b'; expected an operator or 'then'"),
    ("1 +\n  ", "2:3", "unexpected end of expression; expected a value"),
    ("a +\n  * b", "2:3", "unexpected '*'; expected a value"),
]


@pytest.mark.parametrize("text,position,message", SYNTAX)
def test_syntax_errors(text, position, message):
    with pytest.raises(ExprSyntaxError) as e:
        parse(text)
    line, column = map(int, position.split(":"))
    assert (e.value.line, e.value.column) == (line, column)
    assert e.value.text == text
    assert e.value.message.startswith(message), e.value.message
    assert str(e.value).startswith(f"{position}: {message}")
    assert isinstance(e.value, ExprError) and isinstance(e.value, ValueError)


def test_literal_text_hint_only_for_prose():
    with pytest.raises(ExprSyntaxError) as e:
        parse("unlabelled here")
    assert e.value.message.endswith("; if you meant literal text, quote it inside the YAML value: '\"...\"'")
    for text in ("a < b < c", "steps.x.", "[1 2]"):
        with pytest.raises(ExprSyntaxError) as e:
            parse(text)
        assert "literal text" not in e.value.message


@pytest.mark.parametrize(
    "text,span",
    [
        ("if a then b elze c", (12, 16)),  # the offending token
        ("1 +", (3, 3)),  # end of input: empty span after the last character
        ('"unterminated', (0, 1)),  # the unexpected character
        ('x + "\\q"', (4, 8)),  # the string token holding the invalid escape
    ],
)
def test_syntax_error_spans(text, span):
    with pytest.raises(ExprSyntaxError) as e:
        parse(text)
    assert e.value.span == span


def test_precedence():
    add = parse("1 + 2 * 3")
    assert isinstance(add, Binary) and add.op == "+"
    assert isinstance(add.right, Binary) and add.right.op == "*"
    # `not` binds looser than comparisons and `in`.
    assert parse("not a == b") == Unary(1, 1, "not", Binary(1, 5, "==", Name(1, 5, "a"), Name(1, 10, "b")))
    assert parse("not a in b") == Unary(1, 1, "not", Binary(1, 5, "in", Name(1, 5, "a"), Name(1, 10, "b")))
    assert parse("a not in b") == Binary(1, 1, "not in", Name(1, 1, "a"), Name(1, 10, "b"))
    # and binds tighter than or; both are left-associative.
    or_ = parse("a or b and c or d")
    assert or_.op == "or" and or_.left.op == "or" and or_.left.right.op == "and"
    sub = parse("- 2 - -3")
    assert sub == Binary(1, 1, "-", Unary(1, 1, "-", Lit(1, 3, 2)), Unary(1, 7, "-", Lit(1, 8, 3)))


def test_conditional_and_postfix_shapes():
    cond = parse('if a then 1 elif b then 2 else "c"')
    assert isinstance(cond, Cond)
    assert [(c.id, v.value) for c, v in cond.arms] == [("a", 1), ("b", 2)]
    assert cond.orelse.value == "c"
    assert isinstance(parse("(if a then 1 else 2) + 1"), Binary)
    assert isinstance(parse("[if a then 1 else 2]").items[0], Cond)

    chain = parse('edges["validate.done"][0].taken')
    assert isinstance(chain, Member) and chain.name == "taken"
    assert isinstance(chain.obj, Index) and chain.obj.index == Lit(1, 24, 0)
    assert chain.obj.obj.index.value == "validate.done"

    call = parse("f(a, 1,)")
    assert isinstance(call, Call) and call.func == "f" and len(call.args) == 2
    assert parse("now()").args == ()


def test_literals():
    assert parse("[1, 2, 3,]") == ListLit(1, 1, (Lit(1, 2, 1), Lit(1, 5, 2), Lit(1, 8, 3)))
    assert parse("[]").items == ()
    obj = parse('{a: 1, "b c": [true, null],}')
    assert isinstance(obj, ObjectLit)
    assert [key for key, _ in obj.pairs] == ["a", "b c"]
    assert parse("{}").pairs == ()
    for text, value in [
        ("0", 0), ("42", 42), ("1.5", 1.5), ("1e3", 1000.0), ("1.5e2", 150.0), ("2E-1", 0.2),
        ("true", True), ("false", False), ("null", None),
        ("'it\\'s'", "it's"), ('"\\u00e9"', "é"), ('"a\\n\\t\\"b\\\\"', 'a\n\t"b\\'), ("'\\/'", "/"),
    ]:
        node = parse(text)
        assert isinstance(node, Lit) and node.value == value and type(node.value) is type(value), text


def test_keywords_are_member_names_after_dot():
    node = parse("steps.x.outputs.in + steps.x.outputs.if + steps.x.outputs.true + steps.x.outputs.not")
    names = []
    while isinstance(node, Binary):
        names.append(node.right.name)
        node = node.left
    names.append(node.name)
    assert names[::-1] == ["in", "if", "true", "not"]
    # Where the keyword itself is not acceptable the contextual lexer reads a name, which no scope defines (the
    # reference checks report it as E-REF-NAME); where it is acceptable it stays a keyword.
    assert parse("in") == Name(1, 1, "in")
    with pytest.raises(ExprSyntaxError):
        parse("a and")


def test_positions_and_spans():
    node = parse("steps.x + y")
    assert (node.right.line, node.right.column, node.right.span) == (1, 11, (10, 11))
    assert node.left.span == (0, 7)
    assert node.span == (0, 11)
    index = parse('a["k"][0]')
    assert index.span == (0, 9)  # includes the closing bracket
    multi = parse("a +\n  b")
    assert (multi.right.line, multi.right.column, multi.right.span) == (2, 3, (6, 7))
    call = parse("  len(x)")
    assert (call.column, call.span) == (3, (2, 8))


def test_spans_do_not_affect_equality():
    assert parse("a+b") == Binary(1, 1, "+", Name(1, 1, "a"), Name(1, 3, "b"))


def test_parse_is_cached():
    text = "steps.cached.outputs.x and true"
    assert parse(text) is parse(text)


def test_no_python_eval_anywhere_in_the_expression_package():
    package = Path(wynd.spec.expr.__file__).parent
    for path in package.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"eval", "exec", "compile"}, f"{path.name} calls {node.func.id}"

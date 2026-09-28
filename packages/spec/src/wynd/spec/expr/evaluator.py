"""Parsing and tree-walking evaluation of expressions; no `eval` (PLAN §3.5; $DRAFTS/01 §7.1–§7.8)."""

import functools
import operator
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from lark import Lark, Token, Transformer, v_args
from lark.exceptions import UnexpectedCharacters, UnexpectedInput, UnexpectedToken, VisitError

from wynd.spec.expr.errors import EvalError, ExprSyntaxError
from wynd.spec.expr.grammar import GRAMMAR
from wynd.spec.expr.nodes import Binary, Call, Cond, Index, ListLit, Lit, Member, Name, Node, ObjectLit, Unary
from wynd.spec.expr.scope import EdgeState, Scope

# Builtin function name -> (min args, max args) ($DRAFTS/01 §7.6).
BUILTINS: dict[str, tuple[int, int]] = {
    "len": (1, 1),
    "lower": (1, 1),
    "upper": (1, 1),
    "contains": (2, 2),
    "startswith": (2, 2),
    "join": (1, 2),
    "split": (1, 2),
    "default": (2, 2),
    "coalesce": (1, 99),
    "now": (0, 0),
}

# Built once; every parse call keeps its own parser state, so the instance is shared across threads.
_PARSER = Lark(GRAMMAR, parser="lalr", propagate_positions=True, maybe_placeholders=True)


# --------------------------------------------------------------------------------------------------- parsing

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "/": "/"}
_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|.)")


def _unquote(token: Token, text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        sequence = match.group(1)
        if len(sequence) == 5:
            return chr(int(sequence[1:], 16))
        if sequence in _ESCAPES:
            return _ESCAPES[sequence]
        raise ExprSyntaxError(
            text, token.line, token.column, f"invalid escape \\{sequence}", span=(token.start_pos, token.end_pos)
        )

    return _ESCAPE.sub(replace, str(token)[1:-1])


def _node(cls: type[Node], meta: Any, *fields: Any) -> Node:
    return cls(meta.line, meta.column, *fields, span=(meta.start_pos, meta.end_pos))


class _ToAst(Transformer):
    def __init__(self, text: str):
        super().__init__()
        self.text = text

    @v_args(meta=True)
    def string(self, meta, children):
        return _node(Lit, meta, _unquote(children[0], self.text))

    @v_args(meta=True)
    def number(self, meta, children):
        text = str(children[0])
        return _node(Lit, meta, float(text) if any(c in text for c in ".eE") else int(text))

    @v_args(meta=True)
    def true(self, meta, children):
        return _node(Lit, meta, True)

    @v_args(meta=True)
    def false(self, meta, children):
        return _node(Lit, meta, False)

    @v_args(meta=True)
    def null(self, meta, children):
        return _node(Lit, meta, None)

    @v_args(meta=True)
    def name(self, meta, children):
        return _node(Name, meta, str(children[0]))

    @v_args(meta=True)
    def call(self, meta, children):
        return _node(Call, meta, str(children[0]), tuple(c for c in children[1:] if c is not None))

    @v_args(meta=True)
    def list_(self, meta, children):
        return _node(ListLit, meta, tuple(c for c in children if c is not None))

    @v_args(meta=True)
    def object_(self, meta, children):
        return _node(ObjectLit, meta, tuple(c for c in children if c is not None))

    def pair(self, children):
        key, value = children
        return (_unquote(key, self.text) if key.type == "STRING" else str(key), value)

    @v_args(meta=True)
    def member(self, meta, children):
        return _node(Member, meta, children[0], str(children[1]))

    @v_args(meta=True)
    def index(self, meta, children):
        return _node(Index, meta, children[0], children[1])

    @v_args(meta=True)
    def neg(self, meta, children):
        return _node(Unary, meta, "-", children[0])

    @v_args(meta=True)
    def not_(self, meta, children):
        return _node(Unary, meta, "not", children[0])

    @v_args(meta=True)
    def conditional(self, meta, children):
        *arms, orelse = children
        return _node(Cond, meta, tuple(zip(arms[0::2], arms[1::2])), orelse)


def _binary_rule(op: str):
    @v_args(meta=True)
    def rule(self, meta, children):
        return _node(Binary, meta, op, children[0], children[1])

    return rule


for _rule, _op in {
    "or_": "or", "and_": "and", "eq": "==", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">=", "in_": "in",
    "not_in": "not in", "add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%",
}.items():
    setattr(_ToAst, _rule, _binary_rule(_op))


@functools.lru_cache(maxsize=4096)
def parse(text: str) -> Node:
    """Parse (lru-cached); raises ExprSyntaxError."""
    try:
        tree = _PARSER.parse(text)
    except UnexpectedInput as e:
        raise _syntax_error(e, text) from None
    try:
        return _ToAst(text).transform(tree)
    except VisitError as e:
        if isinstance(e.orig_exc, ExprSyntaxError):
            raise e.orig_exc from None
        raise


# Binary operators by their text; "-" and "not" also start a value, so accepting them says nothing about operators.
_OPERATORS = {"+", "*", "/", "%", "==", "!=", "<", "<=", ">", ">=", "in", "and", "or"}
_CLOSERS = ("elif", "else", "then", ")", "]", "}", ",", ":")
_TERMINAL_TEXT = {terminal.name: terminal.pattern.value for terminal in _PARSER.terminals}
_NOT_PROSE = set("\"'()[]{}<>=!+-*/%.,")
_LITERAL_HINT = "; if you meant literal text, quote it inside the YAML value: '\"...\"'"


def _expected(accepts: set[str]) -> str:
    """The expected-clause, from the exact set of acceptable terminals ($DRAFTS/01 §7.8)."""
    texts = {_TERMINAL_TEXT.get(t, t) for t in accepts}
    parts = []
    if accepts == {"NAME"}:
        parts.append("a name")
    elif accepts & {"NAME", "NUMBER", "STRING"}:
        parts.append("a value")
    if texts & _OPERATORS:
        parts.append("an operator")
    parts += [f"'{closer}'" for closer in _CLOSERS if closer in texts]
    if "$END" in accepts:
        parts.append("the end of the expression")
    match parts:
        case []:
            return ""
        case [only]:
            return f"; expected {only}"
        case [*first, last]:
            return f"; expected {', '.join(first)} or {last}"


def _syntax_error(e: UnexpectedInput, text: str) -> ExprSyntaxError:
    if isinstance(e, UnexpectedCharacters):
        pos = e.pos_in_stream
        return ExprSyntaxError(text, e.line, e.column, f"unexpected character {text[pos]!r}", span=(pos, pos + 1))
    accepts = set(e.interactive_parser.accepts()) if isinstance(e, UnexpectedToken) else set()
    hint = _LITERAL_HINT if " " in text.strip() and not _NOT_PROSE & set(text) else ""
    if isinstance(e, UnexpectedToken) and e.token.type != "$END":
        token = e.token
        message = f"unexpected {str(token)!r}{_expected(accepts)}{hint}"
        return ExprSyntaxError(text, token.line, token.column, message, span=(token.start_pos, token.end_pos))
    # End of input is reported just after the last character, not at Lark's last-token position.
    line = text.count("\n") + 1
    column = len(text) - text.rfind("\n")
    message = f"unexpected end of expression{_expected(accepts)}{hint}"
    return ExprSyntaxError(text, line, column, message, span=(len(text), len(text)))


# --------------------------------------------------------------------------------------------------- values

def truthy(value: Any) -> bool:
    """null, false, 0, 0.0, "", [] and {} are false."""
    match value:
        case None:
            return False
        case bool():
            return value
        case int() | float():
            return value != 0
        case str() | list() | dict():
            return len(value) > 0
    return True


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _type(value: Any) -> str:
    match value:
        case None:
            return "null"
        case bool():
            return "boolean"
        case int() | float():
            return "number"
        case str():
            return "string"
        case list():
            return "list"
        case dict():
            return "object"
    return type(value).__name__


def strict_eq(a: Any, b: Any) -> bool:
    """Structural equality; int/float compare numerically; bool is never a number."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_num(a) and _is_num(b):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(strict_eq(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(strict_eq(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


# --------------------------------------------------------------------------------------------------- evaluation

class _View:
    """An intermediate of a `steps`/`edges`/`env` reference chain; never the value of an expression."""

    need = ""


class _Steps(_View):
    need = "steps needs a step key: steps.<key>.outputs, .exit or .runs"


class _Edges(_View):
    need = 'edges needs an edge key: edges["<step>.<exit>"][<index>].taken'


class _Env(_View):
    need = "env needs a variable name: env.NAME"


@dataclass(frozen=True)
class _Edge(_View):
    key: str
    state: EdgeState
    need = 'edges["<step>.<exit>"] needs a branch: [<index>].taken or .<name>.taken'


@dataclass(frozen=True)
class _Branch(_View):
    taken: int
    need = "branch counters only have .taken"


_STEPS, _EDGES, _ENV = _Steps(), _Edges(), _Env()


def _root(name: str, node: Node, scope: Scope) -> Any:
    match name:
        case "steps":
            return _STEPS
        case "edges":
            return _EDGES
        case "env":
            return _ENV
        case "process":
            return {"inputs": scope.process_inputs}
        case "run":
            return {"id": scope.run_id}
        case "previous":
            if scope.previous is None:
                return None
            state = scope.steps[scope.previous]
            return {"outputs": state.outputs, "summary": state.summary}
    raise EvalError(f"unknown name {name!r}", node)


def _get(obj: Any, key: Any, node: Node, scope: Scope) -> Any:
    match obj:
        case None:
            return None
        case _Steps():
            state = scope.steps.get(key) if isinstance(key, str) else None
            if state is None:
                raise EvalError(f"unknown step {key!r}", node)
            return {"runs": state.runs, "exit": state.exit, "outputs": state.outputs}
        case _Edges():
            state = scope.edges.get(key) if isinstance(key, str) else None
            if state is None:
                raise EvalError(f"unknown edge {key!r}", node)
            return _Edge(key, state)
        case _Edge(key=edge, state=state):
            if _is_int(key):
                if 0 <= key < len(state.taken):
                    return _Branch(state.taken[key])
                raise EvalError(f"edge {edge!r} has no branch {key}", node)
            if isinstance(key, str) and key in state.names:
                return _Branch(state.taken[state.names[key]])
            raise EvalError(f"edge {edge!r} has no branch named {key!r}", node)
        case _Branch(taken=taken):
            if key == "taken":
                return taken
            raise EvalError(f"branch counters only have .taken, not {key!r}", node)
        case _Env():
            if not isinstance(key, str):
                raise EvalError(f"env[...] needs a string, got {_type(key)}", node)
            return scope.env.get(key)
        case dict():
            if not isinstance(key, str):
                raise EvalError(f"object keys are strings, got {_type(key)}", node)
            return obj.get(key)
        case list():
            if not _is_int(key):
                raise EvalError(f"list index must be an integer, got {_type(key)}", node)
            return obj[key] if -len(obj) <= key < len(obj) else None
    raise EvalError(f"cannot access {key!r} on a {_type(obj)}", node)


def _access(node: Node, scope: Scope) -> Any:
    """Like _ev, but a reference chain may stop at a view."""
    match node:
        case Name(id=name):
            return _root(name, node, scope)
        case Member(obj=obj, name=name):
            return _get(_access(obj, scope), name, node, scope)
        case Index(obj=obj, index=index):
            return _get(_access(obj, scope), _ev(index, scope), node, scope)
    return _ev(node, scope)


def _contains(container: Any, item: Any, node: Node) -> bool:
    match container:
        case None:
            return False
        case str():
            if not isinstance(item, str):
                raise EvalError(f"'in' a string needs a string, got {_type(item)}", node)
            return item in container
        case list():
            return any(strict_eq(item, x) for x in container)
        case dict():
            return isinstance(item, str) and item in container
    raise EvalError(f"'in' needs a string, list or object on the right, got {_type(container)}", node)


_ORDER = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}
_ARITHMETIC = {"-": operator.sub, "*": operator.mul, "/": operator.truediv, "%": operator.mod}


def _binary(op: str, a: Any, b: Any, node: Node) -> Any:
    match op:
        case "==":
            return strict_eq(a, b)
        case "!=":
            return not strict_eq(a, b)
        case "in":
            return _contains(b, a, node)
        case "not in":
            return not _contains(b, a, node)
        case "<" | "<=" | ">" | ">=":
            if not ((_is_num(a) and _is_num(b)) or (isinstance(a, str) and isinstance(b, str))):
                raise EvalError(f"cannot compare {_type(a)} {op} {_type(b)}", node)
            return _ORDER[op](a, b)
        case "+":
            if (
                (_is_num(a) and _is_num(b))
                or (isinstance(a, str) and isinstance(b, str))
                or (isinstance(a, list) and isinstance(b, list))
            ):
                return a + b
            raise EvalError(f"cannot add {_type(a)} and {_type(b)}", node)
    if not (_is_num(a) and _is_num(b)):
        raise EvalError(f"operator {op!r} needs numbers, got {_type(a)} and {_type(b)}", node)
    if op in ("/", "%") and b == 0:
        raise EvalError("division by zero", node)
    return _ARITHMETIC[op](a, b)


def _call(node: Call, scope: Scope) -> Any:
    name, args = node.func, node.args
    if name not in BUILTINS:
        raise EvalError(f"unknown function {name!r}", node)
    low, high = BUILTINS[name]
    if not low <= len(args) <= high:
        expected = low if low == high else f"{low}..{high}"
        raise EvalError(f"{name}() takes {expected} argument(s), got {len(args)}", node)
    # default/coalesce evaluate lazily: a fallback is only evaluated when it is needed.
    match name:
        case "default":
            value = _ev(args[0], scope)
            return _ev(args[1], scope) if value is None else value
        case "coalesce":
            for arg in args:
                value = _ev(arg, scope)
                if value is not None:
                    return value
            return None
        case "now":
            return scope.clock().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    values = [_ev(arg, scope) for arg in args]
    match name:
        case "len":
            (x,) = values
            if x is None:
                return 0
            if isinstance(x, (str, list, dict)):
                return len(x)
            raise EvalError(f"len() of {_type(x)}", node)
        case "lower" | "upper":
            (x,) = values
            if x is None:
                return None
            if not isinstance(x, str):
                raise EvalError(f"{name}() needs a string, got {_type(x)}", node)
            return x.lower() if name == "lower" else x.upper()
        case "contains":
            container, item = values
            return _contains(container, item, node)
        case "startswith":
            text, prefix = values
            if text is None:
                return False
            if not (isinstance(text, str) and isinstance(prefix, str)):
                raise EvalError(f"startswith() needs strings, got {_type(text)} and {_type(prefix)}", node)
            return text.startswith(prefix)
        case "join":
            items, separator = values[0], values[1] if len(values) == 2 else ","
            if items is None:
                return ""
            if not (isinstance(items, list) and all(isinstance(i, str) for i in items) and isinstance(separator, str)):
                raise EvalError("join() needs a list of strings and a string separator", node)
            return separator.join(items)
        case "split":
            text = values[0]
            if text is None:
                return []
            if not isinstance(text, str):
                raise EvalError(f"split() needs a string, got {_type(text)}", node)
            if len(values) == 1:
                return text.split()
            separator = values[1]
            if not isinstance(separator, str) or separator == "":
                raise EvalError("split() separator must be a non-empty string", node)
            return text.split(separator)
    raise AssertionError(name)


def _ev(node: Node, scope: Scope) -> Any:
    match node:
        case Lit(value=value):
            return value
        case Name() | Member() | Index():
            value = _access(node, scope)
            if isinstance(value, _View):
                raise EvalError(value.need, node)
            return value
        case ListLit(items=items):
            return [_ev(item, scope) for item in items]
        case ObjectLit(pairs=pairs):
            return {key: _ev(value, scope) for key, value in pairs}
        case Cond(arms=arms, orelse=orelse):
            for condition, value in arms:
                if truthy(_ev(condition, scope)):
                    return _ev(value, scope)
            return _ev(orelse, scope)
        case Unary(op="not", operand=operand):
            return not truthy(_ev(operand, scope))
        case Unary(operand=operand):
            value = _ev(operand, scope)
            if not _is_num(value):
                raise EvalError(f"cannot negate {_type(value)}", node)
            return -value
        case Binary(op="and", left=left, right=right):
            return truthy(_ev(left, scope)) and truthy(_ev(right, scope))
        case Binary(op="or", left=left, right=right):
            return truthy(_ev(left, scope)) or truthy(_ev(right, scope))
        case Binary(op=op, left=left, right=right):
            return _binary(op, _ev(left, scope), _ev(right, scope), node)
        case Call():
            return _call(node, scope)
    raise AssertionError(node)


def evaluate(expr: str | Node, scope: Scope) -> Any:
    """Raises EvalError (carrying the expression text), or ExprSyntaxError for a text that does not parse."""
    node = parse(expr) if isinstance(expr, str) else expr
    try:
        return _ev(node, scope)
    except EvalError as e:
        if e.text is None and isinstance(expr, str):
            e.text = expr
        raise


def evaluate_condition(when: str | bool | None, scope: Scope) -> bool:
    """None -> True; bool as-is; str -> truthy(evaluate(...))."""
    match when:
        case None:
            return True
        case bool():
            return when
    return truthy(evaluate(when, scope))


def evaluate_value(value: Any, scope: Scope) -> Any:
    """str -> expression; dict/list recurse; date/datetime -> ISO string; other scalars literal."""
    match value:
        case str():
            return evaluate(value, scope)
        case dict():
            return {key: evaluate_value(item, scope) for key, item in value.items()}
        case list():
            return [evaluate_value(item, scope) for item in value]
        case datetime() | date():
            return value.isoformat()
    return value


def evaluate_with(with_: Mapping[str, Any], scope: Scope) -> dict[str, Any]:
    return {field: evaluate_value(value, scope) for field, value in with_.items()}


def evaluate_limit(value: int | float | str | None, scope: Scope) -> int | float | None:
    """str -> an expression that must yield a number."""
    if not isinstance(value, str):
        return value
    result = evaluate(value, scope)
    if not _is_num(result):
        raise EvalError(f"a limit must evaluate to a number, got {_type(result)}", parse(value), value)
    return result

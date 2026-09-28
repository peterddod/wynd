"""Spike: reference implementation of wynd.spec.expr (parse, AST, evaluate, references)."""
from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from lark import Lark, Token, Transformer, v_args
from lark.exceptions import UnexpectedInput, UnexpectedCharacters, UnexpectedEOF, UnexpectedToken

GRAMMAR = Path(__file__).with_name("grammar.lark").read_text()
_parser = Lark(GRAMMAR, parser="lalr", propagate_positions=True, maybe_placeholders=True)

ROOTS = ("steps", "edges", "process", "env", "run", "previous")
KEYWORDS = {"if", "then", "elif", "else", "and", "or", "not", "in", "true", "false", "null"}


# ---------------------------------------------------------------- AST
@dataclass(frozen=True)
class Node:
    line: int
    column: int


@dataclass(frozen=True)
class Lit(Node):
    value: Any


@dataclass(frozen=True)
class Name(Node):
    id: str


@dataclass(frozen=True)
class Member(Node):
    obj: Node
    name: str


@dataclass(frozen=True)
class Index(Node):
    obj: Node
    index: Node


@dataclass(frozen=True)
class Call(Node):
    func: str
    args: tuple[Node, ...]


@dataclass(frozen=True)
class Unary(Node):
    op: str  # "-" | "not"
    operand: Node


@dataclass(frozen=True)
class Binary(Node):
    op: str  # + - * / % == != < <= > >= in "not in" and or
    left: Node
    right: Node


@dataclass(frozen=True)
class Cond(Node):
    arms: tuple[tuple[Node, Node], ...]
    orelse: Node


@dataclass(frozen=True)
class ListLit(Node):
    items: tuple[Node, ...]


@dataclass(frozen=True)
class ObjectLit(Node):
    pairs: tuple[tuple[str, Node], ...]


class ExprSyntaxError(ValueError):
    def __init__(self, text: str, line: int, column: int, message: str):
        self.text, self.line, self.column = text, line, column
        super().__init__(f"{line}:{column}: {message}")


class EvalError(ValueError):
    def __init__(self, message: str, node: Node | None = None):
        self.node = node
        pos = f"{node.line}:{node.column}: " if node else ""
        super().__init__(pos + message)


_ESC = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'", "/": "/"}


def _unquote(tok: str) -> str:
    body = tok[1:-1]

    def rep(m: re.Match[str]) -> str:
        s = m.group(1)
        if s[0] == "u":
            return chr(int(s[1:], 16))
        if s in _ESC:
            return _ESC[s]
        raise ValueError(f"invalid escape \\{s}")

    return re.sub(r"\\(u[0-9a-fA-F]{4}|.)", rep, body)


def _pos(meta_or_tok) -> tuple[int, int]:
    return meta_or_tok.line, meta_or_tok.column


class _ToAst(Transformer):
    def __default__(self, data, children, meta):  # pragma: no cover
        raise AssertionError(data)

    @v_args(meta=True)
    def string(self, meta, c):
        return Lit(*_pos(meta), _unquote(str(c[0])))

    @v_args(meta=True)
    def number(self, meta, c):
        s = str(c[0])
        return Lit(*_pos(meta), float(s) if any(ch in s for ch in ".eE") else int(s))

    @v_args(meta=True)
    def true(self, meta, c):
        return Lit(*_pos(meta), True)

    @v_args(meta=True)
    def false(self, meta, c):
        return Lit(*_pos(meta), False)

    @v_args(meta=True)
    def null(self, meta, c):
        return Lit(*_pos(meta), None)

    @v_args(meta=True)
    def name(self, meta, c):
        return Name(*_pos(meta), str(c[0]))

    @v_args(meta=True)
    def call(self, meta, c):
        args = tuple(a for a in c[1:] if a is not None)
        return Call(*_pos(meta), str(c[0]), args)

    @v_args(meta=True)
    def list_(self, meta, c):
        return ListLit(*_pos(meta), tuple(a for a in c if a is not None))

    @v_args(meta=True)
    def object_(self, meta, c):
        return ObjectLit(*_pos(meta), tuple(p for p in c if p is not None))

    def pair(self, c):
        k = c[0]
        key = _unquote(str(k)) if k.type == "STRING" else str(k)
        return (key, c[1])

    @v_args(meta=True)
    def member(self, meta, c):
        return Member(*_pos(meta), c[0], str(c[1]))

    @v_args(meta=True)
    def index(self, meta, c):
        return Index(*_pos(meta), c[0], c[1])

    @v_args(meta=True)
    def neg(self, meta, c):
        return Unary(*_pos(meta), "-", c[0])

    @v_args(meta=True)
    def not_(self, meta, c):
        return Unary(*_pos(meta), "not", c[0])

    @v_args(meta=True)
    def conditional(self, meta, c):
        *pairs, orelse = c
        arms = tuple((pairs[i], pairs[i + 1]) for i in range(0, len(pairs), 2))
        return Cond(*_pos(meta), arms, orelse)


def _bin(op):
    @v_args(meta=True)
    def f(self, meta, c):
        return Binary(*_pos(meta), op, c[0], c[1])

    return f


for _rule, _op in {
    "or_": "or", "and_": "and", "eq": "==", "ne": "!=", "lt": "<", "le": "<=", "gt": ">", "ge": ">=",
    "in_": "in", "not_in": "not in", "add": "+", "sub": "-", "mul": "*", "div": "/", "mod": "%",
}.items():
    setattr(_ToAst, _rule, _bin(_op))


@functools.lru_cache(maxsize=4096)
def parse(text: str) -> Node:
    try:
        tree = _parser.parse(text)
    except UnexpectedInput as e:
        line, col = e.line, e.column
        if isinstance(e, UnexpectedEOF) or (isinstance(e, UnexpectedToken) and e.token.type == "$END"):
            line = text.count("\n") + 1
            col = len(text) - (text.rfind("\n") + 1) + 1
        raise ExprSyntaxError(text, line, col, _describe(e, text)) from None
    try:
        return _ToAst().transform(tree)
    except Exception as e:  # invalid escape
        cause = e.__context__ or e
        raise ExprSyntaxError(text, 1, 1, str(cause)) from None


_OPS = {"PLUS", "STAR", "SLASH", "PERCENT", "AND", "OR", "LESSTHAN", "MORETHAN", "IN", "NOT",
        "__ANON_0", "__ANON_1", "__ANON_2", "__ANON_3"}
_PUNCT = (("ELIF", "'elif'"), ("ELSE", "'else'"), ("THEN", "'then'"), ("RPAR", "')'"), ("RSQB", "']'"),
          ("RBRACE", "'}'"), ("COMMA", "','"), ("COLON", "':'"))


def _expected_clause(acc: set[str]) -> str:
    parts = []
    if acc == {"NAME"}:
        parts.append("a name")
    elif acc & {"NAME", "NUMBER", "STRING"}:
        parts.append("a value")
    if acc & _OPS:
        parts.append("an operator")
    parts += [label for t, label in _PUNCT if t in acc]
    if "$END" in acc:
        parts.append("the end of the expression")
    if not parts:
        return ""
    return "; expected " + (", ".join(parts[:-1]) + " or " + parts[-1] if len(parts) > 1 else parts[0])


def _describe(e: UnexpectedInput, text: str) -> str:
    if isinstance(e, UnexpectedCharacters):
        return f"unexpected character {text[e.pos_in_stream]!r}"
    acc = set(e.interactive_parser.accepts()) if isinstance(e, UnexpectedToken) else set()
    what = "end of expression" if isinstance(e, UnexpectedEOF) or e.token.type == "$END" else repr(str(e.token))
    msg = f"unexpected {what}{_expected_clause(acc)}"
    if " " in text.strip() and not any(c in text for c in "\"'()[]{}<>=!+-*/%.,"):
        msg += "; if you meant literal text, quote it inside the YAML value: '\"...\"'"
    return msg


# ---------------------------------------------------------------- scope / values
@dataclass
class StepState:
    runs: int = 0
    exit: str | None = None
    outputs: dict[str, Any] | None = None


@dataclass
class Scope:
    steps: Mapping[str, StepState]
    edges: Mapping[str, Sequence[int]] = field(default_factory=dict)          # "a.b" -> taken per branch index
    branch_names: Mapping[str, Mapping[str, int]] = field(default_factory=dict)  # "a.b" -> {name: index}
    process_inputs: Mapping[str, Any] = field(default_factory=dict)
    env: Mapping[str, str] = field(default_factory=dict)
    run_id: str = ""
    previous_outputs: dict[str, Any] | None = None
    previous_summary: dict[str, Any] | None = None
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    volatile: list[str] = field(default_factory=list)


def truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, (str, list, dict)):
        return len(v) > 0
    return True


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _type(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if _is_num(v):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "list"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def strict_eq(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if _is_num(a) and _is_num(b):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(strict_eq(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(strict_eq(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


# root "views": resolved lazily so that only referenced things are touched
class _EdgeView:
    def __init__(self, key, taken, names):
        self.key, self.taken, self.names = key, taken, names


class _BranchView:
    def __init__(self, taken):
        self.taken = taken


def _root(name: str, scope: Scope, node: Node):
    if name == "steps":
        return {k: {"runs": s.runs, "exit": s.exit, "outputs": s.outputs} for k, s in scope.steps.items()}
    if name == "edges":
        return {"__edges__": True}
    if name == "process":
        return {"inputs": dict(scope.process_inputs)}
    if name == "env":
        return {"__env__": True}
    if name == "run":
        scope.volatile.append(scope.run_id)
        return {"id": scope.run_id}
    if name == "previous":
        return {"outputs": scope.previous_outputs, "summary": scope.previous_summary}
    if name in KEYWORDS:
        raise EvalError(f"unexpected keyword {name!r}", node)
    raise EvalError(f"unknown name {name!r}", node)


def evaluate(expr: str | Node, scope: Scope) -> Any:
    node = parse(expr) if isinstance(expr, str) else expr
    return _ev(node, scope)


def _get(obj, key, node, scope):
    if obj is None:
        return None
    if isinstance(obj, dict) and obj.get("__env__"):
        if not isinstance(key, str):
            raise EvalError("env[...] needs a string", node)
        return scope.env.get(key)
    if isinstance(obj, dict) and obj.get("__edges__"):
        if not isinstance(key, str):
            raise EvalError('edges[...] needs a "step.exit" string', node)
        if key not in scope.edges:
            raise EvalError(f"unknown edge {key!r}", node)
        return _EdgeView(key, scope.edges[key], scope.branch_names.get(key, {}))
    if isinstance(obj, _EdgeView):
        if isinstance(key, int) and not isinstance(key, bool):
            if not 0 <= key < len(obj.taken):
                raise EvalError(f"edge {obj.key!r} has no branch {key}", node)
            return _BranchView(obj.taken[key])
        if isinstance(key, str) and key in obj.names:
            return _BranchView(obj.taken[obj.names[key]])
        raise EvalError(f"edge {obj.key!r} has no branch named {key!r}", node)
    if isinstance(obj, _BranchView):
        if key == "taken":
            return obj.taken
        raise EvalError(f"branch counters only have .taken, not {key!r}", node)
    if isinstance(obj, dict):
        if not isinstance(key, str):
            raise EvalError(f"object keys are strings, got {_type(key)}", node)
        return obj.get(key)
    if isinstance(obj, list):
        if not (isinstance(key, int) and not isinstance(key, bool)):
            raise EvalError(f"list index must be an integer, got {_type(key)}", node)
        return obj[key] if -len(obj) <= key < len(obj) else None
    raise EvalError(f"cannot access {key!r} on a {_type(obj)}", node)


def _ev(n: Node, s: Scope) -> Any:
    match n:
        case Lit(value=v):
            return v
        case Name(id=i):
            return _root(i, s, n)
        case Member(obj=o, name=k):
            return _get(_ev(o, s), k, n, s)
        case Index(obj=o, index=i):
            return _get(_ev(o, s), _ev(i, s), n, s)
        case ListLit(items=items):
            return [_ev(i, s) for i in items]
        case ObjectLit(pairs=pairs):
            return {k: _ev(v, s) for k, v in pairs}
        case Cond(arms=arms, orelse=orelse):
            for c, v in arms:
                if truthy(_ev(c, s)):
                    return _ev(v, s)
            return _ev(orelse, s)
        case Unary(op="not", operand=o):
            return not truthy(_ev(o, s))
        case Unary(op="-", operand=o):
            v = _ev(o, s)
            if not _is_num(v):
                raise EvalError(f"cannot negate {_type(v)}", n)
            return -v
        case Binary(op="and", left=l, right=r):
            return truthy(_ev(l, s)) and truthy(_ev(r, s))
        case Binary(op="or", left=l, right=r):
            return truthy(_ev(l, s)) or truthy(_ev(r, s))
        case Binary(op=op, left=l, right=r):
            return _binop(op, _ev(l, s), _ev(r, s), n)
        case Call(func=f, args=args):
            return _call(f, [_ev(a, s) for a in args], n, s)
    raise AssertionError(n)


def _contains(container, item, n):
    if container is None:
        return False
    if isinstance(container, str):
        if not isinstance(item, str):
            raise EvalError(f"'in' a string needs a string, got {_type(item)}", n)
        return item in container
    if isinstance(container, list):
        return any(strict_eq(item, x) for x in container)
    if isinstance(container, dict):
        return isinstance(item, str) and item in container
    raise EvalError(f"'in' needs a string, list or object on the right, got {_type(container)}", n)


def _binop(op, a, b, n):
    if op == "==":
        return strict_eq(a, b)
    if op == "!=":
        return not strict_eq(a, b)
    if op in ("in", "not in"):
        r = _contains(b, a, n)
        return r if op == "in" else not r
    if op in ("<", "<=", ">", ">="):
        if not ((_is_num(a) and _is_num(b)) or (isinstance(a, str) and isinstance(b, str))):
            raise EvalError(f"cannot compare {_type(a)} {op} {_type(b)}", n)
        return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    if op == "+":
        if (_is_num(a) and _is_num(b)) or (isinstance(a, str) and isinstance(b, str)) or (
            isinstance(a, list) and isinstance(b, list)
        ):
            return a + b
        raise EvalError(f"cannot add {_type(a)} and {_type(b)}", n)
    if not (_is_num(a) and _is_num(b)):
        raise EvalError(f"operator {op!r} needs numbers, got {_type(a)} and {_type(b)}", n)
    if op in ("/", "%") and b == 0:
        raise EvalError("division by zero", n)
    return {"-": lambda: a - b, "*": lambda: a * b, "/": lambda: a / b, "%": lambda: a % b}[op]()


ARITY = {"len": (1, 1), "lower": (1, 1), "upper": (1, 1), "contains": (2, 2), "startswith": (2, 2),
         "join": (1, 2), "split": (1, 2), "default": (2, 2), "coalesce": (1, 99), "now": (0, 0)}


def _call(f, args, n, s: Scope):
    if f not in ARITY:
        raise EvalError(f"unknown function {f!r}", n)
    lo, hi = ARITY[f]
    if not lo <= len(args) <= hi:
        raise EvalError(f"{f}() takes {lo if lo == hi else f'{lo}..{hi}'} argument(s), got {len(args)}", n)
    match f:
        case "len":
            (x,) = args
            if x is None:
                return 0
            if isinstance(x, (str, list, dict)):
                return len(x)
            raise EvalError(f"len() of {_type(x)}", n)
        case "lower" | "upper":
            (x,) = args
            if x is None:
                return None
            if not isinstance(x, str):
                raise EvalError(f"{f}() needs a string, got {_type(x)}", n)
            return x.lower() if f == "lower" else x.upper()
        case "contains":
            return _contains(args[0], args[1], n)
        case "startswith":
            x, p = args
            if x is None:
                return False
            if not (isinstance(x, str) and isinstance(p, str)):
                raise EvalError("startswith() needs strings", n)
            return x.startswith(p)
        case "join":
            xs, sep = (args + [","])[:2]
            if xs is None:
                return ""
            if not isinstance(xs, list) or not all(isinstance(x, str) for x in xs) or not isinstance(sep, str):
                raise EvalError("join() needs a list of strings and a string separator", n)
            return sep.join(xs)
        case "split":
            x = args[0]
            if x is None:
                return []
            if not isinstance(x, str):
                raise EvalError(f"split() needs a string, got {_type(x)}", n)
            if len(args) == 1:
                return x.split()
            if not isinstance(args[1], str) or args[1] == "":
                raise EvalError("split() separator must be a non-empty string", n)
            return x.split(args[1])
        case "default":
            return args[1] if args[0] is None else args[0]
        case "coalesce":
            return next((a for a in args if a is not None), None)
        case "now":
            v = s.clock().astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
            s.volatile.append(v)
            return v
    raise AssertionError(f)


# ---------------------------------------------------------------- references
@dataclass(frozen=True)
class Ref:
    root: str
    path: tuple[str | int | None, ...]  # None = dynamic subscript
    line: int
    column: int
    guarded: bool = False  # inside first arg of default()/coalesce()

    def __str__(self):
        out = self.root
        for p in self.path:
            out += f"[{p}]" if isinstance(p, int) else ("[*]" if p is None else f".{p}")
        return out


def _chain(n: Node):
    """Return (root Name, path) if n is a pure reference chain, else None."""
    path: list = []
    while True:
        match n:
            case Name(id=i):
                return n, tuple(reversed(path))
            case Member(obj=o, name=k):
                path.append(k)
                n = o
            case Index(obj=o, index=Lit(value=v)) if isinstance(v, (str, int)) and not isinstance(v, bool):
                path.append(v)
                n = o
            case Index(obj=o):
                path.append(None)
                n = o
            case _:
                return None


def references(expr: str | Node) -> list[Ref]:
    node = parse(expr) if isinstance(expr, str) else expr
    out: list[Ref] = []
    _refs(node, out, False)
    return out


def _refs(n: Node, out: list[Ref], guarded: bool):
    ch = _chain(n) if isinstance(n, (Name, Member, Index)) else None
    if ch is not None:
        root, path = ch
        out.append(Ref(root.id, path, n.line, n.column, guarded))
        # dynamic subscripts contain their own references
        m = n
        while not isinstance(m, Name):
            if isinstance(m, Index) and not isinstance(m.index, Lit):
                _refs(m.index, out, guarded)
            m = m.obj
        return
    match n:
        case Member(obj=o) | Index(obj=o):
            _refs(o, out, guarded)
            if isinstance(n, Index):
                _refs(n.index, out, guarded)
        case Call(func=f, args=args):
            for i, a in enumerate(args):
                _refs(a, out, guarded or (f == "default" and i == 0) or f == "coalesce")
        case Unary(operand=o):
            _refs(o, out, guarded)
        case Binary(left=l, right=r):
            _refs(l, out, guarded)
            _refs(r, out, guarded)
        case Cond(arms=arms, orelse=e):
            for c, v in arms:
                _refs(c, out, guarded)
                _refs(v, out, guarded)
            _refs(e, out, guarded)
        case ListLit(items=items):
            for i in items:
                _refs(i, out, guarded)
        case ObjectLit(pairs=pairs):
            for _, v in pairs:
                _refs(v, out, guarded)


def evaluate_value(value: Any, scope: Scope) -> Any:
    """`with:` trees: strings are expressions, other scalars literal, containers recurse."""
    if isinstance(value, str):
        return evaluate(value, scope)
    if isinstance(value, dict):
        return {k: evaluate_value(v, scope) for k, v in value.items()}
    if isinstance(value, list):
        return [evaluate_value(v, scope) for v in value]
    return value

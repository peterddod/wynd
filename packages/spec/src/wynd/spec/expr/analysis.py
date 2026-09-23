"""Static analysis of expressions: references, process-independent checks, exit-aware reference checks
(PLAN §3.5; $DRAFTS/01 §7.9).

Diagnostics produced here are relative to the expression: `line`/`column` are 1-based within the expression text and
`span` holds the 0-based [start, end) offsets of the offending node; callers locate them in a document (`loc`, file
position via the source map).
"""

import difflib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from wynd.spec.errors import Diagnostic, Loc
from wynd.spec.expr.errors import ExprSyntaxError
from wynd.spec.expr.evaluator import BUILTINS, parse
from wynd.spec.expr.nodes import Binary, Call, Cond, Index, ListLit, Lit, Member, Name, Node, ObjectLit, Unary

_ROOTS = ("steps", "edges", "process", "env", "run", "previous")
_STEP_MEMBERS = ("outputs", "exit", "runs")
_SUMMARY_FIELDS = ("step", "exit", "key_outputs", "note")


def _render(path: Sequence[str | int | None]) -> str:
    """("items", 0, "sku") -> "items[0].sku"; a dynamic subscript renders as [*]."""
    text = ""
    for part in path:
        match part:
            case None:
                text += "[*]"
            case int():
                text += f"[{part}]"
            case _:
                text += f".{part}" if text else part
    return text


@dataclass(frozen=True)
class Ref:
    root: str  # "steps" | "edges" | "process" | "env" | "run" | "previous" | other name
    path: tuple[str | int | None, ...]  # member names / literal subscripts; None = dynamic subscript
    line: int
    column: int
    guarded: bool = False  # inside default()'s 1st arg or any coalesce() arg

    def __str__(self) -> str:
        return _render((self.root, *self.path))


# --------------------------------------------------------------------------------------------------- extraction

def _key(accessor: Member | Index) -> str | int | None:
    match accessor:
        case Member(name=name):
            return name
        case Index(index=Lit(value=str() | int() as value)) if not isinstance(value, bool):
            return value
    return None


def _chain(node: Node) -> tuple[Name, list[Member | Index]] | None:
    """(root name, accessors in source order) when `node` is a pure reference chain."""
    accessors = []
    while isinstance(node, (Member, Index)):
        accessors.append(node)
        node = node.obj
    if not isinstance(node, Name):
        return None
    return node, accessors[::-1]


def _collect(node: Node, guarded: bool, refs: list[tuple[Ref, Node]], calls: list[Call]) -> None:
    chain = _chain(node)
    if chain is not None:
        root, accessors = chain
        refs.append((Ref(root.id, tuple(_key(a) for a in accessors), node.line, node.column, guarded), node))
        for accessor in accessors:
            if isinstance(accessor, Index) and _key(accessor) is None:
                _collect(accessor.index, guarded, refs, calls)
        return
    match node:
        case Member(obj=obj):
            _collect(obj, guarded, refs, calls)
        case Index(obj=obj, index=index):
            _collect(obj, guarded, refs, calls)
            _collect(index, guarded, refs, calls)
        case Call(func=func, args=args):
            calls.append(node)
            for position, arg in enumerate(args):
                _collect(arg, guarded or func == "coalesce" or (func == "default" and position == 0), refs, calls)
        case Unary(operand=operand):
            _collect(operand, guarded, refs, calls)
        case Binary(left=left, right=right):
            _collect(left, guarded, refs, calls)
            _collect(right, guarded, refs, calls)
        case Cond(arms=arms, orelse=orelse):
            for condition, value in arms:
                _collect(condition, guarded, refs, calls)
                _collect(value, guarded, refs, calls)
            _collect(orelse, guarded, refs, calls)
        case ListLit(items=items):
            for item in items:
                _collect(item, guarded, refs, calls)
        case ObjectLit(pairs=pairs):
            for _, value in pairs:
                _collect(value, guarded, refs, calls)


def _scan(node: Node) -> tuple[list[tuple[Ref, Node]], list[Call]]:
    refs: list[tuple[Ref, Node]] = []
    calls: list[Call] = []
    _collect(node, False, refs, calls)
    return refs, calls


def references(expr: str | Node) -> list[Ref]:
    """Maximal reference chains in source order; a dynamic subscript's own references follow its chain."""
    node = parse(expr) if isinstance(expr, str) else expr
    return [ref for ref, _ in _scan(node)[0]]


def value_references(value: Any) -> list[tuple[Loc, Ref]]:
    """References of every string leaf of a with-tree; Loc relative to it."""
    match value:
        case str():
            return [((), ref) for ref in references(value)]
        case dict():
            return [((key, *loc), ref) for key, item in value.items() for loc, ref in value_references(item)]
        case list():
            return [((index, *loc), ref) for index, item in enumerate(value) for loc, ref in value_references(item)]
    return []


def env_names(exprs: Iterable[str]) -> set[str]:
    """Names referenced via env.X / env["X"]."""
    return {
        ref.path[0]
        for expr in exprs
        for ref in references(expr)
        if ref.root == "env" and ref.path and isinstance(ref.path[0], str)
    }


# --------------------------------------------------------------------------------------------------- checks

def _diagnostic(code: str, message: str, node: Node) -> Diagnostic:
    return Diagnostic("error", code, message, line=node.line, column=node.column, span=node.span)


def _did_you_mean(word: Any, options: Iterable[str]) -> str:
    matches = difflib.get_close_matches(str(word), list(options), n=1)
    return f" (did you mean {matches[0]!r}?)" if matches else ""


def _shape_error(ref: Ref) -> str | None:
    path = ref.path
    match ref.root:
        case "steps":
            if not path or not isinstance(path[0], str):
                return "steps needs a step key: steps.<key>.outputs, .exit or .runs"
            if len(path) == 1:
                return None
            key, member = path[0], path[1]
            if member not in _STEP_MEMBERS:
                return f"steps.{key} has no member {member!r}; use .outputs, .exit or .runs"
            if member != "outputs" and len(path) > 2:
                return f"steps.{key}.{member} has no members"
        case "edges":
            if not path or not isinstance(path[0], str):
                return 'edges needs a literal edge key: edges["<step>.<exit>"][<index>].taken'
            if len(path) < 2 or path[1] is None:
                return f'edges["{path[0]}"] needs a branch: [<index>].taken or .<name>.taken'
            if path[2:] != ("taken",):
                return "branch counters only have .taken"
        case "process":
            if not path or path[0] != "inputs":
                return "process has only .inputs: process.inputs.<field>"
        case "env":
            if len(path) != 1 or not isinstance(path[0], str):
                return 'env needs exactly one variable name: env.NAME or env["NAME"]'
        case "run":
            if path != ("id",):
                return "run has only .id"
        case "previous":
            if not path or path[0] not in ("outputs", "summary"):
                return "previous needs .outputs or .summary"
    return None


def _analyse(expr: str) -> tuple[list[Diagnostic], list[tuple[Ref, Node]]]:
    """Process-independent findings, and the references whose root and shape are valid."""
    try:
        node = parse(expr)
    except ExprSyntaxError as e:
        return [Diagnostic("error", "E-EXPR-SYNTAX", e.message, line=e.line, column=e.column, span=e.span)], []
    refs, calls = _scan(node)
    diagnostics = []
    valid = []
    for ref, ref_node in refs:
        if ref.root not in _ROOTS:
            message = (
                f"unknown name {ref.root!r}; to pass the literal text, quote it inside the YAML value: "
                f"'\"{ref.root}\"'"
            )
            diagnostics.append(_diagnostic("E-REF-NAME", message, ref_node))
            continue
        problem = _shape_error(ref)
        if problem is not None:
            diagnostics.append(_diagnostic("E-REF-SHAPE", problem, ref_node))
            continue
        valid.append((ref, ref_node))
    for call in calls:
        if call.func not in BUILTINS:
            message = f"unknown function {call.func!r}{_did_you_mean(call.func, BUILTINS)}"
            diagnostics.append(_diagnostic("E-EXPR-FUNC", message, call))
            continue
        low, high = BUILTINS[call.func]
        if not low <= len(call.args) <= high:
            expected = low if low == high else f"{low}..{high}"
            message = f"{call.func}() takes {expected} argument(s), got {len(call.args)}"
            diagnostics.append(_diagnostic("E-EXPR-ARITY", message, call))
    return diagnostics, valid


def _in_source_order(diagnostics: list[Diagnostic]) -> list[Diagnostic]:
    return sorted(diagnostics, key=lambda d: d.span or (0, 0))


def check_expression(expr: str) -> list[Diagnostic]:
    """Process-independent checks: E-EXPR-SYNTAX, E-EXPR-FUNC, E-EXPR-ARITY, E-REF-NAME, E-REF-SHAPE."""
    return _in_source_order(_analyse(expr)[0])


@dataclass(frozen=True)
class StepView:
    exits: Mapping[str, dict]  # exits step k may have taken on a path reaching the site -> that exit's schema
    may_be_unrun: bool = False  # some path reaches the site without k having run


@dataclass(frozen=True)
class TypeEnv:
    steps: Mapping[str, StepView]  # every step key in the process
    edges: Mapping[str, Sequence[str | None]]  # edge key -> branch names by index
    process_inputs: dict  # JSON Schema (ProcessDoc.interface().input)
    previous: StepView | None = None  # the step that completed immediately before the site
    env_declared: frozenset[str] = frozenset()  # names in process.env.vars


def _missing(schema: dict, path: tuple[str | int | None, ...]) -> bool:
    from wynd.spec.schemas import MISSING, schema_at

    # Scope outputs never carry the "exit" discriminator: steps.<k>.exit is the way to read it.
    return path[0] == "exit" or schema_at(schema, path) is MISSING


def _output_problems(view: StepView, who: str, path: tuple, guarded: bool) -> list[tuple[str, str]]:
    problems = []
    if view.may_be_unrun and not guarded:
        message = (
            f"{who} may not have run on every path to this expression; guard with default(...)/coalesce(...) or "
            "restructure"
        )
        problems.append(("E-REF-UNRUN", message))
    if not path or not view.exits:
        return problems
    missing = [exit for exit, schema in view.exits.items() if _missing(schema, path)]
    if len(missing) == len(view.exits):
        message = f"{_render(path)!r} is not an output of {who} (exits: {', '.join(view.exits)})"
        problems.append(("E-REF-FIELD", message))
    elif missing and not guarded:
        exits = ", ".join(repr(exit) for exit in missing)
        message = (
            f"field {_render(path)!r} is not declared on exit{'s' if len(missing) > 1 else ''} {exits} of {who}, "
            "which can reach this expression"
        )
        problems.append(("E-REF-EXIT", message))
    return problems


def _reference_problems(ref: Ref, env: TypeEnv) -> list[tuple[str, str]]:
    """Findings for one reference of valid shape (see _shape_error)."""
    path = ref.path
    match ref.root:
        case "steps":
            view = env.steps.get(path[0])
            if view is None:
                return [("E-REF-STEP", f"unknown step {path[0]!r}{_did_you_mean(path[0], env.steps)}")]
            if path[1:2] == ("outputs",):
                return _output_problems(view, f"step {path[0]!r}", path[2:], ref.guarded)
        case "previous" if env.previous is not None:
            if path[0] == "outputs":
                return _output_problems(env.previous, "the previous step", path[1:], ref.guarded)
            if len(path) > 1 and path[1] not in _SUMMARY_FIELDS:
                fields = ", ".join(_SUMMARY_FIELDS)
                return [("E-REF-FIELD", f"{path[1]!r} is not a field of previous.summary ({fields})")]
            if len(path) > 2 and path[1] != "key_outputs":
                return [("E-REF-FIELD", f"previous.summary.{path[1]} is a string and has no members")]
        case "process":
            from wynd.spec.schemas import MISSING, schema_at

            fields = path[1:]
            if fields and schema_at(env.process_inputs, fields) is MISSING:
                declared = ", ".join(env.process_inputs.get("properties", {}))
                return [("E-REF-INPUT", f"{_render(fields)!r} is not an input of the process (inputs: {declared})")]
        case "edges":
            key, branch = path[0], path[1]
            names = env.edges.get(key)
            if names is None:
                return [("E-REF-EDGE", f"no edge {key!r}{_did_you_mean(key, env.edges)}")]
            if isinstance(branch, int) and not 0 <= branch < len(names):
                return [("E-REF-EDGE", f"edge {key!r} has no branch {branch} (it has {len(names)})")]
            if isinstance(branch, str) and branch not in names:
                return [("E-REF-EDGE", f"edge {key!r} has no branch named {branch!r}")]
    return []


def check_references(expr: str, env: TypeEnv) -> list[Diagnostic]:
    """check_expression's findings plus E-REF-STEP/UNRUN/FIELD/EXIT/INPUT/EDGE against `env`."""
    diagnostics, refs = _analyse(expr)
    for ref, node in refs:
        diagnostics += [_diagnostic(code, message, node) for code, message in _reference_problems(ref, env)]
    return _in_source_order(diagnostics)

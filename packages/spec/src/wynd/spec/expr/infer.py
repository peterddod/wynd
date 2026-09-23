"""Type inference for expressions and assignability of JSON Schemas (M3; PLAN §4.1; $DRAFTS/01 §7.10).

Types are JSON Schemas; `{}` is ANY (unknown). References that `check_references` rejects infer ANY and are not
reported again here: `infer_type` reports only E-TYPE-OP (an operation that fails for every possible operand type)
and W-TYPE-NULL (an operand of an ordering or arithmetic operator that may be null). The validator emits the
assignability codes from `check_assignable`'s result.
"""

import copy
import itertools
import json
from collections.abc import Callable, Sequence
from typing import Any, Literal

from wynd.spec.errors import Diagnostic
from wynd.spec.expr.analysis import StepView, TypeEnv
from wynd.spec.expr.errors import ExprSyntaxError
from wynd.spec.expr.evaluator import BUILTINS, parse
from wynd.spec.expr.nodes import Binary, Call, Cond, Index, ListLit, Lit, Member, Name, Node, ObjectLit, Unary
from wynd.spec.records import Summary
from wynd.spec.schemas import ANY, MISSING, normalize_schema, schema_at

Level = Literal["ok", "warning", "error"]

_NULL = {"type": "null"}
_BOOLEAN = {"type": "boolean"}
_INTEGER = {"type": "integer"}
_NUMBER = {"type": "number"}
_STRING = {"type": "string"}
_NUMERIC = ("integer", "number")
_SUMMARY = normalize_schema(Summary.model_json_schema())
_RANK = {"ok": 0, "warning": 1, "error": 2}
_FORMATS = {"date": "date", "date-time": "datetime", "path": "path"}


# --------------------------------------------------------------------------------------------------- schema helpers

def _json_type(value: Any) -> str:
    match value:
        case None:
            return "null"
        case bool():
            return "boolean"
        case int():
            return "integer"
        case float():
            return "number"
        case str():
            return "string"
        case list():
            return "array"
    return "object"


def _values(schema: dict) -> list | None:
    """The allowed values of a `const`/`enum` schema, else None."""
    if "const" in schema:
        return [schema["const"]]
    values = schema.get("enum")
    return values if isinstance(values, list) else None


def _alternatives(schema: Any) -> list[dict] | None:
    """The schema as a flat list of single-type alternatives, each with a string `type` (null included); None when
    the schema is ANY (`{}`, an unresolved `$ref`, or no type information)."""
    if not isinstance(schema, dict) or "$ref" in schema:
        return None
    members = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(members, list):
        alternatives = []
        for member in members:
            found = _alternatives(member)
            if found is None:
                return None
            alternatives += found
        return alternatives
    kind = schema.get("type")
    if isinstance(kind, list):
        return [{**schema, "type": k} for k in kind]
    if isinstance(kind, str):
        return [schema]
    values = _values(schema)
    if values is not None:
        by_type: dict[str, list] = {}
        for value in values:
            by_type.setdefault(_json_type(value), []).append(value)
        return [_NULL if t == "null" else {"type": t, "enum": vs} for t, vs in by_type.items()]
    if "properties" in schema:
        return [{**schema, "type": "object"}]
    if "items" in schema:
        return [{**schema, "type": "array"}]
    return None


def _has_null(alternatives: list[dict]) -> bool:
    return any(a["type"] == "null" for a in alternatives)


def _union(members: Sequence[Any]) -> dict:
    """anyOf of the members: flattened, identical alternatives merged, null last; ANY if any member is ANY."""
    alternatives: list[dict] = []
    for member in members:
        found = _alternatives(member)
        if found is None:
            return {}
        for alternative in found:
            if alternative not in alternatives:
                alternatives.append(alternative)
    alternatives.sort(key=lambda a: a["type"] == "null")
    match alternatives:
        case []:
            return {}
        case [only]:
            return only
    return {"anyOf": alternatives}


def _closed(properties: dict[str, dict]) -> dict:
    """A closed object whose properties are all present (normalised: `required` sorted, omitted when empty)."""
    schema = {"type": "object", "properties": properties, "required": sorted(properties), "additionalProperties": False}
    if not properties:
        del schema["required"]
    return schema


def _array(items: dict) -> dict:
    return {"type": "array", "items": items} if items else {"type": "array"}


def _render(schema: Any) -> str:
    alternatives = _alternatives(schema)
    if alternatives is None:
        return "any"
    return " | ".join(_render_alternative(a) for a in alternatives)


def _render_alternative(alternative: dict) -> str:
    values = _values(alternative)
    if values is not None:
        return " | ".join(json.dumps(v) for v in values)
    match alternative["type"]:
        case "string":
            return _FORMATS.get(alternative.get("format"), "string")
        case "array":
            items = alternative.get("items")
            return f"list[{_render(items)}]" if items else "list"
        case kind:
            return kind


def _walk(schema: Any, path: Sequence[str | int | None]) -> Any:
    """Type at `path` inside `schema`: ANY (`{}`), MISSING, or a schema that is also nullable when an intermediate
    value may be null (member and index access on null yields null)."""
    nullable = False
    for segment in path:
        alternatives = _alternatives(schema)
        if alternatives is None:
            return {}
        nullable = nullable or _has_null(alternatives)
        schema = schema_at(schema, [segment])
        if schema is MISSING:
            return MISSING
        if schema is ANY or not isinstance(schema, dict):
            return {}
    return _union([schema, _NULL]) if nullable else schema


def _without_exit(schema: dict) -> dict:
    """An exit's model as it appears in the scope: without the "exit" discriminator."""
    properties = schema.get("properties")
    if not isinstance(properties, dict) or "exit" not in properties:
        return schema
    return {**schema, "properties": {k: v for k, v in properties.items() if k != "exit"}}


def _outputs(view: StepView, path: tuple) -> dict:
    """`<step>.outputs<path>` over the exits the step may have taken; null when the step may be unrun or the path is
    missing on some exit."""
    members = []
    nullable = view.may_be_unrun
    for schema in view.exits.values():
        found = MISSING if path[:1] == ("exit",) else _walk(_without_exit(normalize_schema(schema)), path)
        if found is MISSING:
            nullable = True
        else:
            members.append(found)
    if view.exits and not members:
        return {}  # missing on every exit: E-REF-FIELD, reported by check_references
    return _union([*members, _NULL] if nullable else members)


def _exit(view: StepView) -> dict:
    exits = list(view.exits)
    if not exits:
        return _NULL if view.may_be_unrun else {}
    enum = {"enum": exits}
    return {"anyOf": [enum, _NULL]} if view.may_be_unrun else enum


def _coalesce(types: Sequence[dict]) -> dict:
    """The first non-null argument: arguments after one that cannot be null are never reached, and the result is
    null only when every argument may be."""
    members = []
    for schema in types:
        alternatives = _alternatives(schema)
        if alternatives is None:
            return {}
        members += [a for a in alternatives if a["type"] != "null"]
        if not _has_null(alternatives):
            return _union(members)
    return _union([*members, _NULL])


def _sum(a: dict, b: dict) -> dict:
    match a["type"]:
        case "integer" | "number":
            return _INTEGER if a["type"] == b["type"] == "integer" else _NUMBER
        case "string":
            return _STRING
    return _array(_union([a.get("items", {}), b.get("items", {})]))


def _numeric(a: dict) -> bool:
    return a["type"] in _NUMERIC


def _numbers(a: dict, b: dict) -> bool:
    return a["type"] in _NUMERIC and b["type"] in _NUMERIC


def _addable(a: dict, b: dict) -> bool:
    return _numbers(a, b) or a["type"] == b["type"] in ("string", "array")


def _ordered(a: dict, b: dict) -> bool:
    return _numbers(a, b) or a["type"] == b["type"] == "string"


def _contains(item: dict, container: dict) -> bool:
    return container["type"] in ("null", "array", "object") or container["type"] == item["type"] == "string"


def _sized(x: dict) -> bool:
    return x["type"] in ("string", "array", "object", "null")


def _text_or_null(x: dict) -> bool:
    return x["type"] in ("string", "null")


def _prefix_test(s: dict, prefix: dict) -> bool:
    return s["type"] == "null" or s["type"] == prefix["type"] == "string"


def _joinable(xs: dict, separator: dict) -> bool:
    if xs["type"] == "null":
        return True
    items = _alternatives(xs.get("items"))
    strings = items is None or any(a["type"] == "string" for a in items)
    return xs["type"] == "array" and strings and separator["type"] == "string"


# --------------------------------------------------------------------------------------------------- inference

class _Inference:
    def __init__(self, text: str, env: TypeEnv):
        self.text = text
        self.env = env
        self.diagnostics: list[Diagnostic] = []

    def type_of(self, node: Node) -> dict:
        match node:
            case Lit(value=value):
                return {"type": _json_type(value)}
            case Name() | Member() | Index():
                return self.access(node)
            case ListLit(items=items):
                return _array(_union([self.type_of(item) for item in items])) if items else {"type": "array"}
            case ObjectLit(pairs=pairs):
                return _closed({key: self.type_of(value) for key, value in pairs})
            case Cond(arms=arms, orelse=orelse):
                values = []
                for condition, value in arms:
                    self.type_of(condition)
                    values.append(self.type_of(value))
                return _union([*values, self.type_of(orelse)])
            case Unary(op="not", operand=operand):
                self.type_of(operand)
                return _BOOLEAN
            case Unary(operand=operand):
                return self.negate(node, operand)
            case Binary():
                return self.binary(node)
            case Call():
                return self.call(node)
        return {}

    def access(self, node: Node) -> dict:
        path: list[str | int | None] = []
        base = node
        while isinstance(base, (Member, Index)):
            match base:
                case Member(name=name):
                    path.append(name)
                case Index(index=Lit(value=str() | int() as key)) if not isinstance(key, bool):
                    path.append(key)
                case Index(index=index):
                    self.type_of(index)
                    path.append(None)
            base = base.obj
        path.reverse()
        if isinstance(base, Name):
            return self.reference(base.id, tuple(path))
        found = _walk(self.type_of(base), path)
        return {} if found is MISSING else found

    def reference(self, root: str, path: tuple) -> dict:
        env = self.env
        match root:
            case "steps":
                view = env.steps.get(path[0]) if path and isinstance(path[0], str) else None
                if view is None:
                    return {}
                match path[1:]:
                    case ():
                        return _closed({"runs": _INTEGER, "exit": _exit(view), "outputs": _outputs(view, ())})
                    case ("runs",):
                        return _INTEGER
                    case ("exit",):
                        return _exit(view)
                    case ("outputs", *rest):
                        return _outputs(view, tuple(rest))
            case "edges":
                if len(path) == 3 and isinstance(path[0], str) and path[1] is not None and path[2] == "taken":
                    return _INTEGER
            case "process":
                if path[:1] == ("inputs",):
                    found = _walk(normalize_schema(env.process_inputs), path[1:])
                    return {} if found is MISSING else found
            case "env":
                if len(path) == 1 and isinstance(path[0], str):
                    return _STRING if path[0] in env.env_declared else {"anyOf": [_STRING, _NULL]}
            case "run":
                if path == ("id",):
                    return _STRING
            case "previous" if env.previous is not None:
                match path[:1]:
                    case ("outputs",):
                        return _outputs(env.previous, path[1:])
                    case ("summary",):
                        found = _walk(_SUMMARY, path[1:])
                        return {} if found is MISSING else found
        return {}

    def check(
        self,
        node: Node,
        ok: Callable[..., bool],
        operands: Sequence[tuple[Node, dict]],
        message: str,
        null_fails: bool = False,
    ) -> bool:
        """E-TYPE-OP (and False) when every operand's type is known and no combination of their alternatives
        satisfies `ok`; otherwise, when the operation fails on null, W-TYPE-NULL for each operand that may be null."""
        alternatives = [_alternatives(schema) for _, schema in operands]
        if all(a is not None for a in alternatives) and not any(
            ok(*combination) for combination in itertools.product(*alternatives)
        ):
            self.report("error", "E-TYPE-OP", message, node)
            return False
        if null_fails:
            for (operand, _), found in zip(operands, alternatives):
                if found is not None and _has_null(found):
                    snippet = self.text[operand.span[0]:operand.span[1]] if operand.span else "an operand"
                    op = node.op if isinstance(node, (Binary, Unary)) else ""
                    message = f"{snippet} may be null, and '{op}' fails on null; guard it with default(...)"
                    self.report("warning", "W-TYPE-NULL", message, operand)
        return True

    def report(self, severity: Literal["error", "warning"], code: str, message: str, node: Node) -> None:
        self.diagnostics.append(Diagnostic(severity, code, message, line=node.line, column=node.column, span=node.span))

    def negate(self, node: Unary, operand: Node) -> dict:
        schema = self.type_of(operand)
        if not self.check(node, _numeric, [(operand, schema)], f"cannot negate {_render(schema)}", null_fails=True):
            return {}
        alternatives = _alternatives(schema)
        return {} if alternatives is None else _union([a for a in alternatives if _numeric(a)])

    def binary(self, node: Binary) -> dict:
        op = node.op
        left, right = self.type_of(node.left), self.type_of(node.right)
        operands = [(node.left, left), (node.right, right)]
        match op:
            case "and" | "or" | "==" | "!=":
                return _BOOLEAN
            case "in" | "not in":
                self.check_in(node, operands)
                return _BOOLEAN
            case "<" | "<=" | ">" | ">=":
                message = f"cannot compare {_render(left)} {op} {_render(right)}"
                self.check(node, _ordered, operands, message, null_fails=True)
                return _BOOLEAN
            case "+":
                message = f"cannot add {_render(left)} and {_render(right)}"
                if not self.check(node, _addable, operands, message, null_fails=True):
                    return {}
                la, ra = _alternatives(left), _alternatives(right)
                if la is None or ra is None:
                    return {}
                return _union([_sum(a, b) for a in la for b in ra if _addable(a, b)])
        message = f"operator '{op}' needs numbers, got {_render(left)} and {_render(right)}"
        if not self.check(node, _numbers, operands, message, null_fails=True):
            return {}
        if op == "/":
            return _NUMBER
        la, ra = _alternatives(left), _alternatives(right)
        if la is None or ra is None:
            return _NUMBER
        integers = all(a["type"] == "integer" for a in la + ra if a["type"] != "null")
        return _INTEGER if integers else _NUMBER

    def check_in(self, node: Node, operands: Sequence[tuple[Node, dict]]) -> None:
        """`item in container` (also `contains(container, item)`)."""
        (_, item), (_, container) = operands
        found = _alternatives(container) or []
        if any(a["type"] in ("null", "array", "object", "string") for a in found):
            message = f"'in' a string needs a string, got {_render(item)}"
        else:
            message = f"'in' needs a string, list or object on the right, got {_render(container)}"
        self.check(node, _contains, operands, message)

    def call(self, node: Call) -> dict:
        name = node.func
        types = [self.type_of(arg) for arg in node.args]
        if name not in BUILTINS or not BUILTINS[name][0] <= len(types) <= BUILTINS[name][1]:
            return {}  # E-EXPR-FUNC / E-EXPR-ARITY, reported by check_expression
        args = list(zip(node.args, types))
        with_separator = [*args, (node, _STRING)][:2]  # join/split: the default separator is a string
        rendered = " and ".join(_render(t) for t in types)
        match name:
            case "len":
                self.check(node, _sized, args, f"len() of {rendered}")
                return _INTEGER
            case "lower" | "upper":
                if self.check(node, _text_or_null, args, f"{name}() needs a string, got {rendered}"):
                    found = _alternatives(types[0])
                    if found is not None and _has_null(found):
                        return {"anyOf": [_STRING, _NULL]}
                return _STRING
            case "contains":
                self.check_in(node, [args[1], args[0]])
                return _BOOLEAN
            case "startswith":
                self.check(node, _prefix_test, args, f"startswith() needs strings, got {rendered}")
                return _BOOLEAN
            case "join":
                self.check(node, _joinable, with_separator, "join() needs a list of strings and a string separator")
                return _STRING
            case "split":
                found = _alternatives(types[0])
                if found is not None and not any(_text_or_null(a) for a in found):
                    message = f"split() needs a string, got {_render(types[0])}"
                else:
                    message = "split() separator must be a non-empty string"
                self.check(node, _prefix_test, with_separator, message)
                return {"type": "array", "items": _STRING}
            case "default" | "coalesce":
                return _coalesce(types)
            case "now":
                return {"type": "string", "format": "date-time"}
        return {}


def infer_type(expr: str, env: TypeEnv) -> tuple[dict, list[Diagnostic]]:
    """Normalised JSON Schema of the expression's value ({} = unknown) and E-TYPE-OP / W-TYPE-NULL findings.

    Diagnostics are relative to the expression (1-based line/column, 0-based `span`), in source order. An expression
    that does not parse infers `{}` with no findings (E-EXPR-SYNTAX belongs to check_references).
    """
    try:
        node = parse(expr)
    except ExprSyntaxError:
        return {}, []
    inference = _Inference(expr, env)
    schema = copy.deepcopy(inference.type_of(node))  # results share module constants; callers own their copy
    return schema, sorted(inference.diagnostics, key=lambda d: d.span or (0, 0))


# --------------------------------------------------------------------------------------------------- assignability

def _at(where: str, text: str) -> str:
    return f"{where}: {text}" if where else text


def check_assignable(src: dict, dst: dict) -> tuple[Literal["ok", "warning", "error"], str]:
    """Can a value of schema `src` be bound where `dst` is expected ($DRAFTS/01 §7.10)?

    ("ok", "") or (level, message); a message names the nested property path first ("fields.total: ..."). A warning
    caused only by nullability reads "may be null" (the validator reports it as W-TYPE-NULL). Extra properties of
    `src` are never a finding: bindings select fields by name.
    """
    return _assign(normalize_schema(src), normalize_schema(dst), "")


def _assign(src: Any, dst: Any, where: str) -> tuple[Level, str]:
    src_alternatives, dst_alternatives = _alternatives(src), _alternatives(dst)
    if src_alternatives is None or dst_alternatives is None:
        return "ok", ""
    sources = [a for a in src_alternatives if a["type"] != "null"]
    targets = [a for a in dst_alternatives if a["type"] != "null"]
    accepts_null = len(targets) < len(dst_alternatives)
    if not sources:
        return ("ok", "") if accepts_null else ("error", _at(where, f"expected {_render(dst)} got null"))
    if not targets:
        return "error", _at(where, f"expected null got {_render(src)}")
    results = [_best(source, targets, dst, where) for source in sources]
    levels = {level for level, _ in results}
    if levels == {"ok"}:
        level, message = "ok", ""
    elif len(results) == 1:
        level, message = results[0]
    elif levels == {"error"}:
        level, message = "error", _at(where, f"expected {_render(dst)} got {_render(src)}")
    else:
        others = " | ".join(_render_alternative(s) for s, (lvl, _) in zip(sources, results) if lvl != "ok")
        level, message = "warning", _at(where, f"expected {_render(dst)} but may get {others}")
    if len(sources) < len(src_alternatives) and not accepts_null:
        match level:
            case "ok":
                return "warning", _at(where, "may be null")
            case "warning":
                return "warning", f"{message}; {_at(where, 'may be null')}"
    return level, message


def _best(source: dict, targets: list[dict], dst: dict, where: str) -> tuple[Level, str]:
    """The best result of `source` against any of the target alternatives."""
    results = [_pair(source, target, where) for target in targets]
    level, message = min(results, key=lambda r: _RANK[r[0]])
    if level == "error" and len(targets) > 1:
        return "error", _at(where, f"expected {_render(dst)} got {_render_alternative(source)}")
    return level, message


def _pair(src: dict, dst: dict, where: str) -> tuple[Level, str]:
    """One non-null source alternative against one non-null target alternative."""
    kinds = (src["type"], dst["type"])
    if kinds[0] != kinds[1] and kinds not in (("integer", "number"), ("number", "integer")):
        return "error", _at(where, f"expected {_render_alternative(dst)} got {_render_alternative(src)}")
    wanted, offered = _values(dst), _values(src)
    if wanted is not None and offered is not None:
        outside = [v for v in offered if v not in wanted]
        if len(outside) == len(offered):
            return "error", _at(where, f"expected {_render_alternative(dst)} got {_render_alternative(src)}")
        if outside:
            rendered = " | ".join(json.dumps(v) for v in outside)
            return "warning", _at(where, f"expected {_render_alternative(dst)} but may get {rendered}")
    match kinds:
        case ("number", "integer"):
            return "warning", _at(where, "expected integer got number")
        case (_, "string"):
            target = dst.get("format")
            if target in ("date", "date-time") and src.get("format") != target:
                text = f"expected {_render_alternative(dst)} got {_render_alternative(src)} (parsed at run time)"
                return "warning", _at(where, text)
        case (_, "object"):
            return _objects(src, dst, where)
        case (_, "array"):
            return _assign(src.get("items", {}), dst.get("items", {}), f"{where}[]" if where else "items")
    return "ok", ""


def _objects(src: dict, dst: dict, where: str) -> tuple[Level, str]:
    offered, wanted = src.get("properties"), dst.get("properties")
    if not isinstance(offered, dict) or not isinstance(wanted, dict):
        return "ok", ""  # an open object on either side
    missing = [name for name in dst.get("required", []) if name not in offered]
    if missing:
        return "error", _at(where, f"missing {', '.join(missing)}")
    results = [
        _assign(offered[name], schema, f"{where}.{name}" if where else name)
        for name, schema in wanted.items()
        if name in offered
    ]
    level = max((level for level, _ in results), key=_RANK.__getitem__, default="ok")
    return level, "; ".join(message for lvl, message in results if lvl == level and level != "ok")

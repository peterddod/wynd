"""Static (AST-only) lint of step modules against module/class-level state: `L001`–`L005`, all errors
(PLAN §5.1 lint row; `$DRAFTS/02 §3.7`).

- L001 `global` anywhere; `nonlocal` at module level.
- L002 a module-level list/dict/set literal, comprehension or generator that is not an argument of a call (so
  `frozenset({...})` / `tuple([...])` are fine; other calls are L003's); `__all__` is exempt.
- L003 a call evaluated at import time (module-level assignments, expression statements and if/while/for/with
  headers) outside `ALLOWED_CALLS`; decorators, defaults, bases and annotations are definitions, not checked.
- L004 `functools.lru_cache` / `functools.cache` (any import alias) as a decorator or called anywhere.
- L005 a class-body assignment of a mutable value (as L002, or a call outside `ALLOWED_CALLS`) on any class except
  the step attributes `tools`, `context`, `mcp`, `exit_codes` and the bodies of pydantic model classes.

Type expressions are skipped: anything inside a subscript's brackets (`Annotated[float, Field(ge=0)]`,
`Callable[[int], str]`). A module that does not parse raises `SyntaxError`.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

ALLOWED_CALLS = frozenset({
    "re.compile", "frozenset", "tuple", "TypeVar", "NewType", "logging.getLogger", "date", "datetime", "timedelta",
    "Decimal", "Path",
})
# The same constructors spelled through their defining module (`import datetime` + `datetime.date(...)`, aliases).
_ALLOWED_QUALIFIED = frozenset({
    "re.compile", "typing.TypeVar", "typing.NewType", "typing_extensions.TypeVar", "typing_extensions.NewType",
    "logging.getLogger", "datetime.date", "datetime.datetime", "datetime.timedelta", "decimal.Decimal",
    "pathlib.Path",
})
_CACHES = frozenset({"functools.lru_cache", "functools.cache"})
_STEP_ATTRIBUTES = frozenset({"tools", "context", "mcp", "exit_codes"})
_MODEL_BASES = frozenset({"BaseModel", "RootModel"})
_MUTABLE = (ast.List, ast.Dict, ast.Set, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)
_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_HINT = "keep per-run state on the instance and cross-run state in self.runtime.cache"


@dataclass(frozen=True)
class LintIssue:
    path: str
    line: int                            # 1-based
    col: int                             # 1-based
    code: str
    severity: Literal["error"]
    message: str


def check_step_module(source: str, path: str) -> list[LintIssue]:
    tree = ast.parse(source, filename=path)
    lint = _Lint(path, _aliases(tree))
    lint.module(tree)
    lint.caches(tree)
    lint.classes(tree)
    return sorted(lint.issues, key=lambda i: (i.line, i.col, i.code))


class _Lint:
    def __init__(self, path: str, aliases: dict[str, str]) -> None:
        self.path = path
        self.aliases = aliases
        self.issues: list[LintIssue] = []

    def add(self, node: ast.AST, code: str, message: str) -> None:
        self.issues.append(LintIssue(self.path, node.lineno, node.col_offset + 1, code, "error", message))

    def module(self, tree: ast.Module) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                self.add(node, "L001", f"`global {', '.join(node.names)}` writes module state; {_HINT}")
        for stmt in _module_statements(tree.body):
            match stmt:
                case ast.Nonlocal():
                    self.add(stmt, "L001", "`nonlocal` at module level")
                case ast.Assign() | ast.AnnAssign() | ast.AugAssign() if stmt.value is not None:
                    targets = _target_names(stmt)
                    mutable = _mutable(stmt.value)
                    if mutable is not None and targets != {"__all__"}:
                        self.add(mutable, "L002",
                                 f"module-level mutable value assigned to {', '.join(sorted(targets)) or 'a target'}: "
                                 f"wrap it in frozenset(...) or tuple(...), or {_HINT}")
                    self.calls(stmt.value)
                case ast.Expr():
                    self.calls(stmt.value)
                case ast.If() | ast.While():
                    self.calls(stmt.test)
                case ast.For() | ast.AsyncFor():
                    self.calls(stmt.iter)
                case ast.With() | ast.AsyncWith():
                    for item in stmt.items:
                        self.calls(item.context_expr)

    def calls(self, expr: ast.expr) -> None:
        for call in _calls(expr):
            if not self.allowed(call):
                self.add(call, "L003",
                         f"module-level call to {_dotted(call.func) or 'an expression'}(...) runs at import and its "
                         f"result is shared by every run; only immutable constructors "
                         f"({', '.join(sorted(ALLOWED_CALLS))}) may; {_HINT}")

    def caches(self, tree: ast.Module) -> None:
        reported: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, _DEFINITIONS):
                for decorator in node.decorator_list:
                    target = decorator.func if isinstance(decorator, ast.Call) else decorator
                    if self.qualified(target) in _CACHES:
                        reported.add(id(decorator))
                        self.add(decorator, "L004", self.cache_message(target))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and id(node) not in reported and self.qualified(node.func) in _CACHES:
                self.add(node, "L004", self.cache_message(node.func))

    def cache_message(self, target: ast.expr) -> str:
        return f"{self.qualified(target)} caches across runs; use self.runtime.cache (explicit, per step)"

    def classes(self, tree: ast.Module) -> None:
        classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        models: set[str] = set()
        changed = True
        while changed:
            changed = False
            for cls in classes:
                if cls.name not in models and any(_base_name(b) in _MODEL_BASES | models for b in cls.bases):
                    models.add(cls.name)
                    changed = True
        for cls in classes:
            if cls.name in models:
                continue
            for stmt in _module_statements(cls.body):
                if not isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)) or stmt.value is None:
                    continue
                targets = _target_names(stmt)
                if targets and targets <= _STEP_ATTRIBUTES:
                    continue
                if _mutable(stmt.value) is not None or any(not self.allowed(c) for c in _calls(stmt.value)):
                    self.add(stmt, "L005",
                             f"class attribute {', '.join(sorted(targets)) or '?'} of {cls.name} holds a mutable "
                             f"value shared by every run; {_HINT}")

    def allowed(self, call: ast.Call) -> bool:
        return _dotted(call.func) in ALLOWED_CALLS or self.qualified(call.func) in _ALLOWED_QUALIFIED

    def qualified(self, expr: ast.expr) -> str | None:
        """Dotted name with its first segment resolved through the module's imports (`ft.cache` -> functools.cache)."""
        dotted = _dotted(expr)
        if dotted is None:
            return None
        head, _, rest = dotted.partition(".")
        head = self.aliases.get(head, head)
        return f"{head}.{rest}" if rest else head


def _aliases(tree: ast.Module) -> dict[str, str]:
    """Local name -> imported dotted name, from every import in the module."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        match node:
            case ast.Import():
                for alias in node.names:
                    if alias.asname:
                        aliases[alias.asname] = alias.name
                    else:
                        head = alias.name.partition(".")[0]
                        aliases[head] = head
            case ast.ImportFrom(module=str(module), level=0):
                for alias in node.names:
                    aliases[alias.asname or alias.name] = f"{module}.{alias.name}"
    return aliases


def _module_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Statements of this scope, descending into if/try/with/for/while/match blocks but not into definitions."""
    for stmt in body:
        yield stmt
        if isinstance(stmt, _DEFINITIONS):
            continue
        for name in ("body", "orelse", "finalbody"):
            yield from _module_statements(getattr(stmt, name, []))
        for handler in getattr(stmt, "handlers", []):
            yield from _module_statements(handler.body)
        for case in getattr(stmt, "cases", []):
            yield from _module_statements(case.body)


def _target_names(stmt: ast.Assign | ast.AnnAssign | ast.AugAssign) -> set[str]:
    targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
    return {node.id for target in targets for node in ast.walk(target) if isinstance(node, ast.Name)}


def _evaluated(expr: ast.AST) -> Iterator[ast.AST]:
    """Nodes of `expr` evaluated with it, skipping lambda bodies and subscript brackets (type expressions)."""
    yield expr
    for name, child in ast.iter_fields(expr):
        if (isinstance(expr, ast.Lambda) and name == "body") or (isinstance(expr, ast.Subscript) and name == "slice"):
            continue
        for node in child if isinstance(child, list) else [child]:
            if isinstance(node, ast.AST):
                yield from _evaluated(node)


def _mutable(expr: ast.expr) -> ast.AST | None:
    """The first list/dict/set literal, comprehension or generator of `expr` that is not inside a call's arguments."""
    if isinstance(expr, _MUTABLE):
        return expr
    if isinstance(expr, (ast.Call, ast.Lambda)):
        return None
    if isinstance(expr, ast.Subscript):
        return _mutable(expr.value)
    for child in ast.iter_child_nodes(expr):
        found = _mutable(child) if isinstance(child, ast.expr) else None
        if found is not None:
            return found
    return None


def _calls(expr: ast.expr) -> Iterator[ast.Call]:
    return (node for node in _evaluated(expr) if isinstance(node, ast.Call))


def _dotted(expr: ast.expr) -> str | None:
    match expr:
        case ast.Name():
            return expr.id
        case ast.Attribute():
            head = _dotted(expr.value)
            return f"{head}.{expr.attr}" if head else None
    return None


def _base_name(expr: ast.expr) -> str | None:
    match expr:
        case ast.Subscript():
            return _base_name(expr.value)
        case ast.Name():
            return expr.id
        case ast.Attribute():
            return expr.attr
    return None

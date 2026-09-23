"""Parsing and tree-walking evaluation of expressions; no `eval` (PLAN §3.5; $DRAFTS/01 §7.4–§7.7)."""

from collections.abc import Mapping
from typing import Any

from wynd.spec.expr.nodes import Node
from wynd.spec.expr.scope import Scope

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


def parse(text: str) -> Node:
    """Parse (lru-cached); raises ExprSyntaxError."""
    raise NotImplementedError("PLAN §3.5")


def evaluate(expr: str | Node, scope: Scope) -> Any:
    """Raises EvalError."""
    raise NotImplementedError("PLAN §3.5")


def evaluate_condition(when: str | bool | None, scope: Scope) -> bool:
    """None -> True; bool as-is; str -> truthy(evaluate(...))."""
    raise NotImplementedError("PLAN §3.5")


def evaluate_value(value: Any, scope: Scope) -> Any:
    """str -> expression; dict/list recurse; date/datetime -> ISO string; other scalars literal."""
    raise NotImplementedError("PLAN §3.5")


def evaluate_with(with_: Mapping[str, Any], scope: Scope) -> dict[str, Any]:
    raise NotImplementedError("PLAN §3.5")


def evaluate_limit(value: int | float | str | None, scope: Scope) -> int | float | None:
    """str -> an expression that must yield a number."""
    raise NotImplementedError("PLAN §3.5")


def truthy(value: Any) -> bool:
    """null, false, 0, 0.0, "", [] and {} are false."""
    raise NotImplementedError("PLAN §3.5")


def strict_eq(a: Any, b: Any) -> bool:
    """Structural equality; int/float compare numerically; bool is never a number."""
    raise NotImplementedError("PLAN §3.5")

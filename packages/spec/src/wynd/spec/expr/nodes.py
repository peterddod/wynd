"""Expression AST: frozen dataclasses; line/column are 1-based within the expression text (PLAN §3.5;
$DRAFTS/01 §7.3)."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Node:
    line: int
    column: int
    # 0-based [start, end) character offsets of the node inside the expression text (Lark positions); diagnostics
    # carry it as `Diagnostic.span`.
    span: tuple[int, int] | None = field(default=None, kw_only=True, compare=False, repr=False)


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
    arms: tuple[tuple[Node, Node], ...]  # (condition, value) for `if` and each `elif`
    orelse: Node


@dataclass(frozen=True)
class ListLit(Node):
    items: tuple[Node, ...]


@dataclass(frozen=True)
class ObjectLit(Node):
    pairs: tuple[tuple[str, Node], ...]

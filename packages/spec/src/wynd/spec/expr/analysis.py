"""Static analysis of expressions: references, process-independent checks, exit-aware reference checks
(PLAN §3.5; $DRAFTS/01 §7.9)."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from wynd.spec.errors import Diagnostic, Loc
from wynd.spec.expr.nodes import Node


@dataclass(frozen=True)
class Ref:
    root: str  # "steps" | "edges" | "process" | "env" | "run" | "previous" | other name
    path: tuple[str | int | None, ...]  # member names / literal subscripts; None = dynamic subscript
    line: int
    column: int
    guarded: bool = False  # inside default()'s 1st arg or any coalesce() arg


def references(expr: str | Node) -> list[Ref]:
    """Maximal reference chains in source order."""
    raise NotImplementedError("PLAN §3.5")


def value_references(value: Any) -> list[tuple[Loc, Ref]]:
    """References of every string leaf of a with-tree; Loc relative to it."""
    raise NotImplementedError("PLAN §3.5")


def env_names(exprs: Iterable[str]) -> set[str]:
    """Names referenced via env.X / env["X"]."""
    raise NotImplementedError("PLAN §3.5")


def check_expression(expr: str) -> list[Diagnostic]:
    """Process-independent checks: E-EXPR-SYNTAX, E-EXPR-FUNC, E-EXPR-ARITY, E-REF-NAME, E-REF-SHAPE."""
    raise NotImplementedError("PLAN §3.5")


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


def check_references(expr: str, env: TypeEnv) -> list[Diagnostic]:
    """check_expression's findings plus E-REF-STEP/UNRUN/FIELD/EXIT/INPUT/EDGE against `env`."""
    raise NotImplementedError("PLAN §3.5")

"""Expression reference checks at every typed site (PLAN §6.1, §6.2, §6.3 pass 6; owner PROC-VAL).

Per-site `TypeEnv` from the dataflow state -> `wynd.spec.expr.check_references`; field-level findings against
`precise=False` interfaces become warnings with the suffix ` [provisional: interface inferred from examples]`;
records `env_refs`. `check_expr_at` serves the web editor (dataflow over the in-memory doc).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from wynd.spec.errors import Diagnostic, Loc
    from wynd.spec.expr.analysis import TypeEnv

    from ..workspace import Workspace


@dataclass(frozen=True)
class TypedSite:                          # produced by the dataflow walk
    process: str
    loc: Loc                              # document path of the value/expression
    expr: str | None                      # None for synthetic sites (entry, on_error, handler exits)
    env: TypeEnv                          # exit-aware scope at that site (WHEN/WITH/FINAL state)
    target: Literal["when", "with", "limit", "exit", "entry", "on_error", "handler_exit", "finally"]
    dst_schema: dict | None               # schema the value must be assignable to (None: condition/limit)
    src_schema: dict | None = None        # synthetic sites: the source schema instead of inferring expr


@dataclass(frozen=True)
class ExprCheckResult:
    errors: list[Diagnostic]              # with `span` inside the expression
    warnings: list[Diagnostic]
    scope: list[str]                      # references valid at `loc` (exit-aware)


def check_expr_at(
    ws: Workspace, pid: str, doc: dict[str, Any], protos: dict[str, Any], loc: Loc, expr: str
) -> ExprCheckResult:
    raise NotImplementedError("PLAN §6.1 check_expr_at")

"""M5 agentic edges (PLAN §5.6; `$DRAFTS/08 §4.4–§4.5`): the worker-side verifier and the executor-side
`WorkerEdgeChecker`. The seam types are imported from `wynd.runtime.executor.edges` and never redefined here."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from wynd.runtime.executor.edges import (
    EdgeCheckCall,
    EdgeCheckCause,
    EdgeChecker,
    EdgeCheckError,
    EdgeCheckResult,
    EdgeVerdict,
    make_edge_check_call,
)
from wynd.runtime.policy import CassetteConfig

if TYPE_CHECKING:
    from wynd.runtime.worker.pool import WorkerPool
    from wynd.spec.plan import RunPlan

__all__ = [
    "EDGE_VERDICT_SCHEMA",
    "VERIFIER_INSTRUCTION",
    "EdgeCheckCall",
    "EdgeCheckCause",
    "EdgeCheckError",
    "EdgeCheckResult",
    "EdgeChecker",
    "EdgeVerdict",
    "WorkerEdgeChecker",
    "handle_edge_check",
    "make_edge_check_call",
    "run_edge_check",
]

EDGE_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"take": {"type": "boolean"}, "reason": {"type": "string", "maxLength": 500}},
    "required": ["take", "reason"],
    "additionalProperties": False,
}

VERIFIER_INSTRUCTION = """\
You are the transition verifier for one edge of an automated process.
Decide whether the process should take the transition described in the input.

Check: {check}

Take the transition (take = true) only if the check is clearly satisfied by the data
provided. If the data is missing, ambiguous or contradicts the check, do not take it
(take = false). Give a one-sentence reason that cites the data.
"""


def run_edge_check(
    call: EdgeCheckCall,
    cassette: CassetteConfig,
    workspace: str,
    on_event: Callable[[dict[str, Any]], None],
) -> EdgeCheckResult:
    """Worker-side check via `complete_structured` (no tools, no MCP, `unit=f"edge:{branch_key}"`); raises
    `EdgeCheckError`."""
    raise NotImplementedError("PLAN §5.6")


def handle_edge_check(params: dict[str, Any]) -> dict[str, Any]:
    """Worker RPC handler for `edge.check`; errors become JSON-RPC `-32001` with `data.cause`."""
    raise NotImplementedError("PLAN §5.6")


class WorkerEdgeChecker:
    """Implements `EdgeChecker` by dispatching `edge.check` to `plan.edge_venvs[f"{pid}:{branch_key}"]`; emits
    nothing."""

    def __init__(self, pool: WorkerPool, plan: RunPlan) -> None:
        self.pool = pool
        self.plan = plan

    def check(
        self,
        call: EdgeCheckCall,
        *,
        cassette: CassetteConfig,
        workspace: str,
        timeout_s: float | None,
        on_event: Callable[[dict[str, Any]], None],
    ) -> EdgeCheckResult:
        raise NotImplementedError("PLAN §5.6")

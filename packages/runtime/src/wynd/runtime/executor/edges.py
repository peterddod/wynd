"""The agentic-edge seam (PLAN §5.4), complete in M1; `wynd.runtime.edges` re-exports it and implements the checker
in M5. Only the executor emits `edge.check`, from the returned `EdgeCheckResult` or the raised `EdgeCheckError`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from wynd.runtime.policy import CassetteConfig
from wynd.runtime.usage import Usage
from wynd.spec.lockfiles import EdgeLockEntry

if TYPE_CHECKING:
    from wynd.runtime.executor.instance import Instance
    from wynd.spec.process_doc import Branch, Edge


@dataclass(frozen=True)
class EdgeVerdict:
    take: bool
    reason: str                              # <= 500 chars


@dataclass(frozen=True)
class EdgeCheckCall:
    run_id: str
    process: str
    process_goal: str | None
    edge: str
    branch: int
    branch_key: str
    source_step: str
    target: str
    check: str
    context: dict[str, Any]
    bindings: dict[str, Any]
    lock: EdgeLockEntry
    provider: str
    model_id: str


@dataclass(frozen=True)
class EdgeCheckResult:
    verdict: EdgeVerdict
    attempts: int
    validation_failures: int
    provider: str
    tier: str
    model_id: str
    usage: Usage
    replayed: bool
    duration_ms: float


EdgeCheckCause = Literal["validation", "transport", "timeout", "config", "model", "cassette_miss"]


class EdgeCheckError(Exception):
    def __init__(self, cause: EdgeCheckCause, message: str) -> None:
        super().__init__(message)
        self.cause = cause
        self.message = message


class EdgeChecker(Protocol):
    def check(
        self,
        call: EdgeCheckCall,
        *,
        cassette: CassetteConfig,
        workspace: str,
        timeout_s: float | None,
        on_event: Callable[[dict[str, Any]], None],
    ) -> EdgeCheckResult: ...                # raises EdgeCheckError


def make_edge_check_call(
    instance: Instance, edge: Edge, branch_index: int, branch: Branch, bindings: dict[str, Any]
) -> EdgeCheckCall:
    """Resolve the lock entry (defaults if missing), provider = entry.provider or plan.provider, model_id via
    `resolve_model`, context from `branch.context` or `["previous.outputs"]` via `assemble_context`."""
    raise NotImplementedError("PLAN §5.4")

"""The agentic-edge seam (PLAN §5.4), complete in M1; `wynd.runtime.edges` re-exports it and implements the checker
in M5. Only the executor emits `edge.check`, from the returned `EdgeCheckResult` or the raised `EdgeCheckError`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from wynd.runtime.policy import CassetteConfig
from wynd.runtime.usage import Usage
from wynd.spec.lockfiles import EdgeLockEntry, branch_key, check_hash

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


DEFAULT_CHECK_CONTEXT = "previous.outputs"          # a branch without `context:` sees the source step's outputs

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
    `resolve_model`, context from `branch.context` or `["previous.outputs"]` via `assemble_context`.

    `plan.provider` is the ROOT's effective default, also for branches inside a child process (PLAN §3.15).
    Raises `StepFailure("config")` for an unknown provider or tier."""
    from wynd.runtime.executor.context import assemble_context
    from wynd.runtime.providers import resolve_model

    key = branch_key(edge.from_, branch_index, branch.name)
    entry = instance.plan.edges_lock.edges.get(key)
    if entry is None:
        entry = EdgeLockEntry(check_hash=check_hash(branch.check, branch.context))
    ctx = instance.ctx
    provider = entry.provider or ctx.plan.provider
    return EdgeCheckCall(
        run_id=ctx.run_id,
        process=instance.plan.id,
        process_goal=instance.plan.definition.goal,
        edge=edge.from_,
        branch=branch_index,
        branch_key=key,
        source_step=edge.source_step,
        target=branch.step,
        check=branch.check,
        context=assemble_context(branch.context or [DEFAULT_CHECK_CONTEXT], instance, ctx),
        bindings=bindings,
        lock=entry,
        provider=provider,
        model_id=resolve_model(provider, entry.tier, ctx.registry),
    )

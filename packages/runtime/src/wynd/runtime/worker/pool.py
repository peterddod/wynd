"""Dispatchers: the subprocess `WorkerPool` (one worker per venv) and the in-process test seam (PLAN §5.3;
`$DRAFTS/02 §6.2`)."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from wynd.runtime.interface import StepDescription
    from wynd.runtime.step import Step
    from wynd.runtime.worker.protocol import RunStepParams, RunStepResult
    from wynd.spec.plan import RunPlan


class Dispatcher(Protocol):
    """What the executor runs steps through."""

    def start(self) -> None: ...

    def describe(self, step_id: str) -> StepDescription: ...

    def dispatch(
        self,
        step_id: str,
        params: RunStepParams,
        *,
        on_event: Callable[[dict[str, Any]], None],
        timeout: float | None,
    ) -> RunStepResult: ...                  # raises StepTimeout, WorkerCrashed, WorkerRpcError

    def close(self) -> None: ...


class WorkerPool:
    """At most one worker per `PlanVenv`, guarded by a per-venv lock; lazy respawn after a crash or timeout."""

    def __init__(self, plan: RunPlan, *, env: Mapping[str, str] | None = None, cwd: str | None = None) -> None:
        raise NotImplementedError("PLAN §5.3")

    def start(self, venvs: Iterable[str] | None = None) -> None:
        raise NotImplementedError("PLAN §5.3")

    def dispatch(
        self,
        step_id: str,
        params: RunStepParams,
        *,
        on_event: Callable[[dict[str, Any]], None],
        timeout: float | None,
    ) -> RunStepResult:
        raise NotImplementedError("PLAN §5.3")

    def describe(self, step_id: str) -> StepDescription:
        raise NotImplementedError("PLAN §5.3")

    def status(self) -> list[dict[str, Any]]:
        """`[{"venv", "pid", "alive"}]` for `/readyz`."""
        raise NotImplementedError("PLAN §5.3")

    def call(
        self,
        venv_id: str,
        method: str,
        params: dict[str, Any],
        *,
        on_event: Callable[[dict[str, Any]], None],
        timeout: float | None,
    ) -> dict[str, Any]:
        raise NotImplementedError("PLAN §5.3")

    def close(self) -> None:
        raise NotImplementedError("PLAN §5.3")


class InProcessDispatcher:
    """Runs `run_chain` in-process for `{step_id: Step subclass}` (unit-test seam; ignores timeouts)."""

    def __init__(self, classes: Mapping[str, type[Step]]) -> None:
        raise NotImplementedError("PLAN §5.3")

    def start(self) -> None:
        raise NotImplementedError("PLAN §5.3")

    def describe(self, step_id: str) -> StepDescription:
        raise NotImplementedError("PLAN §5.3")

    def dispatch(
        self,
        step_id: str,
        params: RunStepParams,
        *,
        on_event: Callable[[dict[str, Any]], None],
        timeout: float | None,
    ) -> RunStepResult:
        raise NotImplementedError("PLAN §5.3")

    def close(self) -> None:
        raise NotImplementedError("PLAN §5.3")

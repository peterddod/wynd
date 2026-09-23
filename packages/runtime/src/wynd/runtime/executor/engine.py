"""`Executor` and `ProcessResult` (PLAN §5.4; algorithm `$DRAFTS/02 §7` over the spec `Scope` and the §3.5 routing
rules). An `Executor` is reusable and thread-safe across concurrent `run()` calls (all run state is local)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from wynd.runtime.usage import Usage
from wynd.spec.records import ProcessError, StepError

if TYPE_CHECKING:
    from wynd.runtime.executor.edges import EdgeChecker
    from wynd.runtime.storage import Stores
    from wynd.runtime.worker.pool import Dispatcher
    from wynd.spec.plan import RunPlan


class ProcessResult(BaseModel):
    run_id: str
    process: str
    exit: str
    outputs: dict[str, Any]
    error: ProcessError | None                     # set whenever the top-level error handler ran
    status: Literal["succeeded", "failed"]         # failed iff exit == "error"
    trace: str
    workspace: str | None
    duration_ms: float
    usage: Usage
    finally_errors: list[StepError] = []


class Executor:
    def __init__(
        self,
        plan: RunPlan,
        dispatcher: Dispatcher,
        stores: Stores,
        *,
        env: Mapping[str, str] | None = None,
        edge_checker: EdgeChecker | None = None,
    ) -> None:
        raise NotImplementedError("PLAN §5.4")

    def validate_inputs(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        """Raises `InvalidProcessInputs` (boundary check, before any run record exists)."""
        raise NotImplementedError("PLAN §5.4")

    def run(
        self,
        inputs: Mapping[str, Any],
        *,
        run_id: str | None = None,
        cassette_mode: Literal["live", "record", "replay"] = "live",
        cassette_root: Path | None = None,
        record_root: Path | None = None,
        cassette_literals: Mapping[str, str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> ProcessResult:
        raise NotImplementedError("PLAN §5.4")

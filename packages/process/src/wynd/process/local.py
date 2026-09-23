"""Run a process locally with venv workers (PLAN §6.1; owner PROC-ENV).

`plan_local` -> `WorkerPool(plan, env=env)` -> `Executor(plan, pool, stores, env=env,
edge_checker=WorkerEdgeChecker(pool, plan))` -> `run`; the pool is closed in `finally`. Relative paths in `inputs`
are not resolved here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from wynd.runtime.edges import WorkerEdgeChecker
from wynd.runtime.executor.engine import Executor
from wynd.runtime.worker.pool import WorkerPool

from .plan import plan_local

if TYPE_CHECKING:
    from wynd.runtime.executor.engine import ProcessResult
    from wynd.runtime.storage import Stores
    from wynd.spec.plan import RunPlan

    from .workspace import Workspace


def run_local(
    ws: Workspace,
    pid: str,
    inputs: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    stores: Stores,
    run_id: str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    cassette_mode: Literal["live", "record", "replay"] = "live",
    cassette_root: Path | None = None,
    record_root: Path | None = None,
    cassette_literals: Mapping[str, str] | None = None,
    metadata: Mapping[str, Any] | None = None,
    venv_root: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> ProcessResult:
    plan = plan_local(ws, pid, venv_root=venv_root, log=log)
    return run_plan(
        plan, inputs, env=env, stores=stores, run_id=run_id, on_event=on_event, cassette_mode=cassette_mode,
        cassette_root=cassette_root, record_root=record_root, cassette_literals=cassette_literals, metadata=metadata,
    )


def run_plan(
    plan: RunPlan,
    inputs: Mapping[str, Any],
    *,
    env: Mapping[str, str],
    stores: Stores,
    run_id: str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    cassette_mode: Literal["live", "record", "replay"] = "live",
    cassette_root: Path | None = None,
    record_root: Path | None = None,
    cassette_literals: Mapping[str, str] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ProcessResult:
    """One run of an already built plan with fresh workers whose environment is `env` (the test runner plans once
    and runs each example with its own env)."""
    pool = WorkerPool(plan, env=env)
    try:
        executor = Executor(plan, pool, stores, env=env, edge_checker=WorkerEdgeChecker(pool, plan))
        return executor.run(
            inputs, run_id=run_id, cassette_mode=cassette_mode, cassette_root=cassette_root, record_root=record_root,
            cassette_literals=cassette_literals, metadata=metadata, on_event=on_event,
        )
    finally:
        pool.close()

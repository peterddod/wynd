"""Run a process locally with venv workers (PLAN §6.1; owner PROC-ENV).

`plan_local` -> `WorkerPool(plan, env=env)` -> `Executor(plan, pool, stores, env=env,
edge_checker=WorkerEdgeChecker(pool, plan))` -> `run`; the pool is closed in `finally`. Relative paths in `inputs`
are not resolved here.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from wynd.runtime.executor.engine import ProcessResult
    from wynd.runtime.storage import Stores

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
    raise NotImplementedError("PLAN §6.1 run_local")

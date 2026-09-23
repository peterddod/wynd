"""Job runner selection (PLAN §8; `$DRAFTS/06 §6.5`). Stub; CTL-JOBS.

`open_job_runner(name, **kw)` loads entry point `name` from group `wynd.job_runners` and calls it with the backend
factory keywords `(*, env, workspace_root, state_dir, stores, handlers=None)`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.process.jobs import JobRunner


def open_job_runner(name: str, **kw: Any) -> JobRunner:
    raise NotImplementedError("PLAN §8")


def pid_alive(pid: int) -> bool:
    """`os.kill(pid, 0)`: ProcessLookupError -> False, PermissionError -> True."""
    raise NotImplementedError("PLAN §8.1")

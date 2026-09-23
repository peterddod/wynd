"""Job runner selection and the behaviour both local runners share (PLAN §3.18, §8; `$DRAFTS/06 §6.5`).

`open_job_runner(name, **kw)` loads entry point `name` from group `wynd.job_runners` and calls it with the backend
factory keywords `(*, env, workspace_root, state_dir, stores, handlers=None)`.

`LocalJobRunner` is the part of `InProcessJobRunner` and `SubprocessJobRunner` that does not depend on where a job
runs: records (`new_job_record` at submit, with the handler fixed from the runner's table), log reads, liveness
(a running job whose host process on this machine is gone is marked failed), cancelling a job that is not running,
and requeueing an answered job under the same id.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wynd.controller.errors import Invalid, JobState
from wynd.controller.jobs import records
from wynd.controller.jobs.handlers import DEFAULT_HANDLERS
from wynd.controller.jobs.harness import job_log_path
from wynd.process import git
from wynd.process.jobs import TERMINAL, new_job_record

if TYPE_CHECKING:
    from wynd.process.jobs import JobKind, JobRecord, JobRunner
    from wynd.runtime.storage import Stores

GROUP = "wynd.job_runners"
WORKER_GONE = "worker exited unexpectedly (see log)"


def open_job_runner(name: str, **kw: Any) -> JobRunner:
    found = list(metadata.entry_points(group=GROUP, name=name))
    if not found:
        known = sorted(ep.name for ep in metadata.entry_points(group=GROUP))
        raise Invalid(f"unknown job runner {name!r} (WYND_JOB_RUNNER); installed: {', '.join(known)}")
    return found[0].load()(**kw)


def _settled(record: JobRecord) -> bool:
    """Finished, or waiting for answers: nothing is running for it."""
    return record.status in TERMINAL or record.status == "awaiting_input"


def pid_alive(pid: int) -> bool:
    """`os.kill(pid, 0)`: ProcessLookupError -> False, PermissionError -> True."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class LocalJobRunner:
    """Shared by the in-process and subprocess runners. Subclasses set `name` and define `_start(job_id)` (run a
    queued job) and `_cancel_running(record)` (stop a running one)."""

    name = "local"

    def __init__(
        self,
        *,
        env: Mapping[str, str],
        workspace_root: Path,
        state_dir: Path,
        stores: Stores,
        handlers: Mapping[str, str] | None = None,
    ) -> None:
        self.env = env
        self.workspace_root = Path(workspace_root).absolute()
        self.state_dir = Path(state_dir).absolute()
        self.stores = stores
        self.handlers = dict(DEFAULT_HANDLERS if handlers is None else handlers)

    def _exited(self, job_id: str) -> bool:
        """Called on every `status`: True when this runner knows the job's host process has exited."""
        return False

    # --- JobRunner --------------------------------------------------------------------------------------------------

    def submit(self, kind: JobKind, ref: str, inputs: Mapping[str, Any]) -> str:
        if kind not in self.handlers:
            raise Invalid(f"no handler for job kind {kind!r}")
        record = new_job_record(kind, ref, inputs, ws_root=self.workspace_root, runner=self.name,
                                handler=self.handlers[kind])
        records.create(self.stores.runs, record)
        self._start(record.id)
        return record.id

    def status(self, job_id: str) -> JobRecord:
        exited = self._exited(job_id)
        record = records.load(self.stores.runs, job_id)
        if _settled(record) or not self._gone(record, exited):
            return record
        record = records.load(self.stores.runs, job_id)          # it may have finished meanwhile
        if _settled(record) or not self._gone(record, exited):
            return record
        return records.update(self.stores.runs, job_id, status="failed", finished_at=datetime.now(UTC),
                              error={"message": WORKER_GONE, "detail": None})

    @staticmethod
    def _gone(record: JobRecord, exited: bool) -> bool:
        """The job's host process is known to have exited: the runner reaped it, or the record names a pid on this
        machine that no longer exists."""
        if exited:
            return True
        if record.status != "running" or record.pid is None or record.host != socket.gethostname():
            return False
        return record.pid != os.getpid() and not pid_alive(record.pid)

    def logs(self, job_id: str, offset: int = 0) -> tuple[str, int, bool]:
        """Log text from byte `offset`; while the job is not done only whole lines are returned."""
        done = _settled(self.status(job_id))
        try:
            with open(job_log_path(self.state_dir, job_id), "rb") as f:
                f.seek(offset)
                data = f.read()
        except FileNotFoundError:
            return "", offset, done
        if not done:
            data = data[: data.rfind(b"\n") + 1]
        return data.decode("utf-8", errors="replace"), offset + len(data), done

    def artefacts(self, job_id: str) -> dict[str, Any]:
        return records.load(self.stores.runs, job_id).artefacts

    def cancel(self, job_id: str) -> None:
        record = self.status(job_id)
        match record.status:
            case "queued" | "awaiting_input":
                records.update(self.stores.runs, job_id, status="cancelled", finished_at=datetime.now(UTC))
                self._after_cancel(record)
            case "running":
                self._cancel_running(record)
            case "cancelled":
                return
            case _:
                raise JobState(f"job {job_id} is already {record.status}")

    def _after_cancel(self, record: JobRecord) -> None:
        """A job cancelled before it ran; its host process (if any was started) has nothing left to do."""

    def requeue(self, job_id: str, ref: str, inputs: Mapping[str, Any]) -> None:
        record = self.status(job_id)
        if record.status != "awaiting_input":
            raise JobState(f"job {job_id} is {record.status}; only a job awaiting input can be resumed")
        records.update(self.stores.runs, job_id, status="queued", ref=git.rev_parse(self.workspace_root, ref),
                       inputs=dict(inputs), attempt=record.attempt + 1, pid=None, finished_at=None, error=None)
        self._start(job_id)

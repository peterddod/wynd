"""`SubprocessJobRunner`: one `python -m wynd.controller.jobs.worker` process per job (PLAN §3.18, §8;
`$DRAFTS/06 §6.5`).

Entry point `wynd.job_runners: subprocess` (the default `WYND_JOB_RUNNER`); cancel sends SIGTERM to the worker's
process group, which the worker turns into `JobCancelled`. The worker runs in its own session with the runner's `env`,
its stdout/stderr appended to the job log; `status` reaps exited workers, and a job whose worker exited without
finishing the record is marked failed.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from wynd.controller.jobs.harness import job_log_path
from wynd.controller.jobs.runners import LocalJobRunner

if TYPE_CHECKING:
    from wynd.process.jobs import JobRecord
    from wynd.runtime.storage import Stores


class SubprocessJobRunner(LocalJobRunner):
    name = "subprocess"

    def __init__(
        self,
        *,
        env: Mapping[str, str],
        workspace_root: Path,
        state_dir: Path,
        stores: Stores,
        handlers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(env=env, workspace_root=workspace_root, state_dir=state_dir, stores=stores, handlers=handlers)
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._exit_codes: dict[str, int] = {}
        self._procs_lock = threading.Lock()

    def _start(self, job_id: str) -> None:
        log_path = job_log_path(self.state_dir, job_id)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        argv = [sys.executable, "-m", "wynd.controller.jobs.worker", "--workspace", str(self.workspace_root),
                "--job", job_id, "--checkout", "worktree"]
        with open(log_path, "ab") as log_fh:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT,
                                    cwd=self.workspace_root, start_new_session=True, env=dict(self.env))
        with self._procs_lock:
            self._procs[job_id] = proc
            self._exit_codes.pop(job_id, None)

    def _exited(self, job_id: str) -> bool:
        """Reap the job's worker if it has exited (so long-lived hosts keep no zombies); True once it has."""
        with self._procs_lock:
            proc = self._procs.get(job_id)
            if proc is not None and proc.poll() is not None:
                self._exit_codes[job_id] = proc.returncode
                del self._procs[job_id]
            return job_id in self._exit_codes

    def _cancel_running(self, record: JobRecord) -> None:
        self._terminate(record.pid)

    def _after_cancel(self, record: JobRecord) -> None:
        with self._procs_lock:
            proc = self._procs.get(record.id)
        if proc is not None and proc.poll() is None:
            self._terminate(proc.pid)

    @staticmethod
    def _terminate(pid: int | None) -> None:
        if pid is None:
            return
        try:
            os.killpg(pid, signal.SIGTERM)          # the worker leads its own session: pgid == pid
        except ProcessLookupError:
            pass

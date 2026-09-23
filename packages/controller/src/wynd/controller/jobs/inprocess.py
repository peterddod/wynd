"""`InProcessJobRunner`: runs `execute_job` in a daemon thread (PLAN §3.18, §8; `$DRAFTS/06 §6.5`).

Entry point `wynd.job_runners: inprocess` (tests); constructed with the backend factory keywords (PLAN §8). A running
in-process job cannot be cancelled (`JobState`). Handlers see this process's `os.environ`.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from wynd.controller.errors import JobState
from wynd.controller.jobs.checkout import WorktreeCheckout
from wynd.controller.jobs.harness import execute_job
from wynd.controller.jobs.runners import LocalJobRunner

if TYPE_CHECKING:
    from wynd.process.jobs import JobRecord


class InProcessJobRunner(LocalJobRunner):
    name = "inprocess"

    def _start(self, job_id: str) -> None:
        threading.Thread(target=self._run, args=(job_id,), daemon=True, name=f"wynd-job-{job_id}").start()

    def _run(self, job_id: str) -> None:
        execute_job(job_id, checkout=WorktreeCheckout(self.workspace_root, self.state_dir), stores=self.stores,
                    registry=self.stores.registry)

    def _cancel_running(self, record: JobRecord) -> None:
        raise JobState("in-process jobs cannot be cancelled while running")

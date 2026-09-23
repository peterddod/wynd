"""`InProcessJobRunner`: runs `execute_job` in a daemon thread (PLAN §3.18, §8; `$DRAFTS/06 §6.5`). Stub; CTL-JOBS.

Entry point `wynd.job_runners: inprocess` (tests); constructed with the backend factory keywords (PLAN §8). A running
in-process job cannot be cancelled (`JobState`).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.process.jobs import JobKind, JobRecord
    from wynd.runtime.storage import Stores


class InProcessJobRunner:
    name = "inprocess"

    def __init__(
        self,
        *,
        env: Mapping[str, str],
        workspace_root: Path,
        state_dir: Path,
        stores: Stores,
        handlers: Mapping[str, str] | None = None,
    ) -> None:
        raise NotImplementedError("PLAN §3.18")

    def submit(self, kind: JobKind, ref: str, inputs: Mapping[str, Any]) -> str:
        raise NotImplementedError("PLAN §3.18")

    def status(self, job_id: str) -> JobRecord:
        raise NotImplementedError("PLAN §3.18")

    def logs(self, job_id: str, offset: int = 0) -> tuple[str, int, bool]:
        raise NotImplementedError("PLAN §3.18")

    def artefacts(self, job_id: str) -> dict[str, Any]:
        raise NotImplementedError("PLAN §3.18")

    def cancel(self, job_id: str) -> None:
        raise NotImplementedError("PLAN §3.18")

    def requeue(self, job_id: str, ref: str, inputs: Mapping[str, Any]) -> None:
        raise NotImplementedError("PLAN §3.18")

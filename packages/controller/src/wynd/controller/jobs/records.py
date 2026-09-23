"""`JobRecord` storage in the `RunRegistry` (kind "job") and the web `Job` projection (PLAN §3.18, §3.21 amendments
3, 10, 12; `$DRAFTS/06 §5.8`, §6.5). Stub; CTL-JOBS."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.controller.models import Job
    from wynd.process.jobs import JobRecord
    from wynd.runtime.storage.base import RunRegistry


def create(runs: RunRegistry, record: JobRecord) -> None:
    """Store a new `JobRecord`."""
    raise NotImplementedError("PLAN §3.18")


def load(runs: RunRegistry, job_id: str) -> JobRecord:
    """`NotFound` for an unknown id or a record that is not a job."""
    raise NotImplementedError("PLAN §3.18")


def update(runs: RunRegistry, job_id: str, **fields: Any) -> JobRecord:
    """Atomic shallow merge; -> the updated `JobRecord`."""
    raise NotImplementedError("PLAN §3.18")


def to_dto(record: JobRecord) -> Job:
    """`Job.kind` = `job_kind`, `process_id` = `process`, `branch` = `result_branch`, `session` via
    `compile_view.session_dto`, `build` (`BuildResult`) from the build artefacts."""
    raise NotImplementedError("PLAN §3.21")

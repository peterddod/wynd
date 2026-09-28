"""`JobRecord` storage in the `RunRegistry` (kind "job") and the web `Job` projection (PLAN §3.18, §3.21 amendments
3, 10, 12; `$DRAFTS/06 §5.8`, §6.5)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_core import to_jsonable_python

from wynd.controller.errors import NotFound
from wynd.controller.models import BuildResult, BuildTests, Job, JobError
from wynd.process.jobs import JobRecord

if TYPE_CHECKING:
    from wynd.runtime.storage.base import RunRegistry


def create(runs: RunRegistry, record: JobRecord) -> None:
    """Store a new `JobRecord`."""
    runs.create(record.model_dump(mode="json"))


def load(runs: RunRegistry, job_id: str) -> JobRecord:
    """`NotFound` for an unknown id or a record that is not a job."""
    data = runs.get(job_id)
    if data is None or data.get("kind") != "job":
        raise NotFound(f"no job '{job_id}'")
    return JobRecord.model_validate(data)


def update(runs: RunRegistry, job_id: str, **fields: Any) -> JobRecord:
    """Atomic shallow merge; -> the updated `JobRecord`."""
    try:
        data = runs.update(job_id, to_jsonable_python(fields))
    except KeyError:
        raise NotFound(f"no job '{job_id}'") from None
    return JobRecord.model_validate(data)


def to_dto(record: JobRecord) -> Job:
    """`Job.kind` = `job_kind`, `process_id` = `process`, `branch` = `result_branch`, `session` via
    `compile_view.session_dto`, `build` (`BuildResult`) from the build artefacts."""
    error = record.error
    return Job(
        id=record.id,
        kind=record.job_kind,
        process_id=record.process,
        ref=record.ref,
        status=record.status,
        created_at=record.created_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
        branch=record.result_branch,
        result_commit=record.result_commit,
        session=_session(record),
        build=_build(record),
        integration=record.integration,
        error=JobError(message=str(error.get("message") or ""), detail=error.get("detail")) if error else None,
        usage=record.usage,
        chat_id=record.chat_id,
        report=record.report,
        artefacts=record.artefacts,
    )


def _session(record: JobRecord) -> dict[str, Any] | None:
    if record.job_kind != "compile" or record.session is None:
        return None
    from wynd.controller.compile_view import session_dto

    dto = session_dto(record.session)
    return None if dto is None else dto.model_dump(mode="json")


def _build(record: JobRecord) -> BuildResult | None:
    """Present once a build job produced its image (the artefacts of PLAN §6.5)."""
    a = record.artefacts
    if record.job_kind != "build" or "image" not in a:
        return None
    tests = a["tests"]
    return BuildResult(
        commit=a["commit"],
        image=a["image"],
        build_dir=a["build_dir"],
        tests=BuildTests(passed=tests["passed"], failed=tests["failed"], total=tests["total"], source=tests["source"]),
        image_digest=a.get("image_digest"),
        pushed=bool(a.get("pushed", False)),
    )

"""Job types (SPEC §6.6; PLAN §3.18; owner PROC-GIT). Runners and the harness live in `wynd.controller.jobs`.

The types are the W0-complete contract. `new_job_record` resolves the full sha, `base_commit`, `workspace_rel` and
`target_branch = inputs["target_branch"]` for commit-producing kinds (`ValueError` if missing; never reads a branch).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel

from wynd.runtime.usage import Usage

if TYPE_CHECKING:
    from wynd.runtime.storage.base import Registry, RunRegistry

JobKind = Literal["compile", "test_live", "build", "bake", "optimise"]
JobStatus = Literal["queued", "running", "awaiting_input", "succeeded", "failed", "cancelled"]
TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})   # awaiting_input = stable wait state


class JobUsage(Usage):                       # totals over the job (Usage §3.13)
    by: dict[str, Usage] = {}                # "<provider>/<tier>" -> Usage


class JobRecord(BaseModel):                  # stored in RunRegistry with kind "job"
    id: str                                  # new_id("job")
    kind: Literal["job"] = "job"
    job_kind: JobKind
    process: str
    ref: str                                 # full sha the checkout is made at (updated on requeue)
    base_commit: str                         # sha at first submit (compile squashes onto it)
    target_branch: str | None = None         # branch to integrate into (commit-producing kinds)
    workspace_rel: str = ""                  # workspace path inside the repo, e.g. "examples/invoices"
    inputs: dict[str, Any]
    status: JobStatus
    runner: str                              # "module:function", fixed at submit
    handler: str                             # "module:function", fixed at submit
    attempt: int = 1
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    cpu_ms: int | None = None
    pid: int | None = None
    host: str | None = None
    result_branch: str | None = None
    result_commit: str | None = None
    artefacts: dict[str, Any] = {}           # kind-specific (build: commit, image, image_digest, build_dir, pushed, tests,
                                             # sizes {build_dir_bytes, image_bytes, wheels_bytes}; bake: path, size_bytes;
                                             # phased build: prepared (Prepared JSON) between the two phases)
    report: dict[str, Any] | None = None     # compile report / TestReport / build summary / optimise result
    session: dict[str, Any] | None = None    # compile SessionData JSON (compiler-owned format)
    questions: list[dict] = []               # pending questions (copied from the session for listing)
    usage: JobUsage = JobUsage()             # model usage only; wall/cpu time live in duration_ms/cpu_ms
    error: dict[str, Any] | None = None      # {"message", "detail"}
    integration: dict[str, Any] | None = None   # IntegrationResult JSON once integrated
    chat_id: str | None = None


@dataclass
class JobContext:                            # built by the harness; everything a handler may use
    job: JobRecord
    inputs: dict[str, Any]                   # = job.inputs
    session: dict | None                     # = job.session from the previous attempt (resume)
    worktree: Path                           # git toplevel of the job checkout (detached at job.ref)
    workspace: Path                          # worktree / job.workspace_rel
    workspace_root: Path                     # the USER's workspace (shared .wynd/venvs, .wynd/build)
    state_dir: Path                          # workspace_root / ".wynd"
    scratch: Path                            # state_dir / "jobs" / <id> / "scratch" (outside the checkout)
    runs: RunRegistry
    registry: Registry                       # user registry
    log: Callable[[str], None]               # appended to .wynd/jobs/<id>/job.log with "HH:MM:SS " prefix
    commit: Callable[[str, Sequence[str] | None], str | None]
    # commit(message, paths=None): stage paths (None = reference closure of job.process at the checkout, incl.
    # deletions), commit on the detached HEAD with identity "Wynd <wynd@localhost>" unless the repo configures one,
    # append trailer "Wynd-Job: <id>"; returns the new sha, or None if nothing staged
    save_session: Callable[[dict], None]     # checkpoint the session into the record (crash safety, live web view)


class JobOutcome(BaseModel):
    status: Literal["succeeded", "failed", "awaiting_input"]
    commit: str | None = None                # the harness publishes it as the job's result branch
    artefacts: dict[str, Any] = {}
    report: dict[str, Any] | None = None
    session: dict[str, Any] | None = None    # required when awaiting_input
    questions: list[dict] = []
    error: str | None = None
    usage: JobUsage = JobUsage()


JobHandler = Callable[[JobContext], JobOutcome]


class JobRunner(Protocol):                   # SPEC §6.6's four operations + cancel + requeue
    name: str

    def submit(self, kind: JobKind, ref: str, inputs: Mapping[str, Any]) -> str: ...

    def status(self, job_id: str) -> JobRecord: ...

    def logs(self, job_id: str, offset: int = 0) -> tuple[str, int, bool]: ...     # (text, next byte offset, done)

    def artefacts(self, job_id: str) -> dict[str, Any]: ...

    def cancel(self, job_id: str) -> None: ...

    def requeue(self, job_id: str, ref: str, inputs: Mapping[str, Any]) -> None: ...  # answering = resubmit the SAME job


def new_job_record(
    kind: JobKind, ref: str, inputs: Mapping[str, Any], *, ws_root: Path, runner: str, handler: str
) -> JobRecord:
    raise NotImplementedError("PLAN §3.18 new_job_record")


def wait_for(
    runner: JobRunner, job_id: str, *, poll_s: float = 0.5, timeout_s: float | None = None
) -> JobRecord:
    raise NotImplementedError("PLAN §3.18 wait_for")

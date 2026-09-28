"""The job harness `execute_job` (PLAN §3.18 "Harness contract"; `$DRAFTS/06 §6.4`).

Load the record; `status=running`; checkout via `CheckoutBackend.prepare`; build `JobContext`; run the handler
(`phase="all"`: the handler table; `"prepare"`: `PHASE_HANDLERS[kind]["prepare"]`, whose result is stored as
`artefacts["prepared"]` while the record stays `running`; `"finalize"`: `PHASE_HANDLERS[kind]["finalize"]`); publish
`result_branch(job_kind, process, id)` at `outcome.commit`; fold the outcome into the record; remove the checkout on
success/awaiting_input and keep it on failure; a handler exception -> `failed` with the traceback in the log.
"""

from __future__ import annotations

import os
import socket
import threading
import time
import traceback
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic_core import to_jsonable_python

from wynd.controller.jobs import records
from wynd.controller.jobs.handlers import PHASE_HANDLERS, resolve_handler
from wynd.process import git
from wynd.process.errors import WyndProcessError
from wynd.process.jobs import JobContext, JobOutcome, JobRecord, JobUsage
from wynd.process.workspace import CommitTree, load_workspace

if TYPE_CHECKING:
    from wynd.controller.jobs.checkout import Checkout, CheckoutBackend
    from wynd.runtime.storage import Stores
    from wynd.runtime.storage.base import Registry


class JobCancelled(Exception):
    """Raised inside a running job when it is cancelled (the subprocess worker maps SIGTERM to it)."""


class JobLog:
    """`.wynd/jobs/<id>/job.log`: every line gets a `HH:MM:SS ` (UTC) prefix; appended across attempts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def line(self, text: str) -> None:
        stamp = f"{datetime.now(UTC):%H:%M:%S} "
        lines = str(text).rstrip("\n").split("\n")
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write("".join(stamp + line + "\n" for line in lines))


def job_log_path(state_dir: Path, job_id: str) -> Path:
    return state_dir / "jobs" / job_id / "job.log"


def execute_job(
    job_id: str,
    *,
    phase: Literal["all", "prepare", "finalize"] = "all",
    checkout: CheckoutBackend,
    stores: Stores,
    registry: Registry,
    handlers: Mapping[str, str] | None = None,
) -> JobRecord:
    """-> the final `JobRecord`. `handlers` overrides the record's handler per job kind (tests).

    `all` and `prepare` start a `queued` job; `finalize` continues a `running` job whose `artefacts["prepared"]` is
    set. Any other record is returned unchanged."""
    runs = stores.runs
    rec = records.load(runs, job_id)
    match phase:
        case "all" | "prepare" if rec.status != "queued":
            return rec
        case "finalize" if rec.status != "running" or "prepared" not in rec.artefacts:
            return rec

    log = JobLog(job_log_path(checkout.state_dir, job_id))
    started = time.monotonic()
    if phase != "finalize":
        if rec.attempt > 1:
            log.line(f"---- attempt {rec.attempt} (resumed at {rec.ref[:7]}) ----")
        rec = records.update(runs, job_id, status="running", started_at=datetime.now(UTC), finished_at=None,
                             pid=os.getpid(), host=socket.gethostname())
    else:
        rec = records.update(runs, job_id, pid=os.getpid(), host=socket.gethostname())
    log.line(f"job {job_id} ({rec.job_kind} {rec.process}) attempt {rec.attempt} phase {phase} at {rec.ref[:12]}")

    co: Checkout | None = None
    try:
        spec = _handler_spec(rec, phase, handlers)
        co = checkout.prepare(rec)
        ctx = _context(rec, co, checkout, runs, registry, log)
        result = resolve_handler(spec)(ctx)
        if phase == "prepare":
            fields = {"artefacts": {**rec.artefacts, "prepared": to_jsonable_python(result)}}
        else:
            fields = _fold(rec, JobOutcome.model_validate(result), co, checkout, log)
    except JobCancelled:
        log.line("job cancelled")
        fields = {"status": "cancelled"}
    except Exception as err:
        detail = traceback.format_exc()
        log.line(detail)
        fields = {"status": "failed", "error": {"message": f"{type(err).__name__}: {err}", "detail": detail}}

    status = fields.get("status", "running")
    if co is not None:
        try:
            checkout.cleanup(co, keep=status == "failed")
        except Exception:
            log.line(f"checkout cleanup failed:\n{traceback.format_exc()}")
    match status:
        case "failed":
            log.line(f"job failed: {fields['error']['message']}")
        case "running":
            log.line("prepared; waiting for finalize")
        case _:
            log.line(f"job {status}")
    fields["duration_ms"] = (rec.duration_ms or 0) + int((time.monotonic() - started) * 1000)
    if status != "running":
        fields["finished_at"] = datetime.now(UTC)
    return records.update(runs, job_id, **fields)


def _handler_spec(rec: JobRecord, phase: str, handlers: Mapping[str, str] | None) -> str:
    if phase == "all":
        return (handlers or {}).get(rec.job_kind) or rec.handler
    phases = PHASE_HANDLERS.get(rec.job_kind)
    if phases is None or phase not in phases:
        raise ValueError(f"{rec.job_kind} jobs have no {phase} phase")
    return phases[phase]


def _context(
    rec: JobRecord, co: Checkout, checkout: CheckoutBackend, runs: Any, registry: Registry, log: JobLog
) -> JobContext:
    scratch = checkout.state_dir / "jobs" / rec.id / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)

    def commit(message: str, paths: Sequence[str] | None = None) -> str | None:
        if paths is None:
            paths = _closure_paths(co.workspace, rec.process)
        return git.commit_paths(co.workspace, message, paths, trailers={"Wynd-Job": rec.id})

    def save_session(session: dict) -> None:
        records.update(runs, rec.id, session=session)

    return JobContext(
        job=rec,
        inputs=rec.inputs,
        session=rec.session,
        worktree=co.worktree,
        workspace=co.workspace,
        workspace_root=checkout.workspace_root,
        state_dir=checkout.state_dir,
        scratch=scratch,
        runs=runs,
        registry=registry,
        log=log.line,
        commit=commit,
        save_session=save_session,
    )


def _closure_paths(workspace: Path, pid: str) -> list[str]:
    """The reference closure of `pid` in the checkout's working tree and at its HEAD, so deletions are staged too."""
    paths: set[str] = set()
    for at_head in (False, True):
        try:
            tree = CommitTree(workspace, "HEAD") if at_head else None
            paths.update(git.reference_closure(load_workspace(workspace, tree), pid))
        except WyndProcessError:                 # the process (or its workspace) is gone on one side
            continue
    return sorted(paths)


def _fold(rec: JobRecord, outcome: JobOutcome, co: Checkout, checkout: CheckoutBackend, log: JobLog) -> dict:
    """The record fields a handler outcome sets; publishes `outcome.commit` as the job's result branch."""
    fields: dict[str, Any] = {
        "status": outcome.status,
        "artefacts": {**rec.artefacts, **outcome.artefacts},
        "report": outcome.report,
        "questions": outcome.questions,
        "usage": _add_usage(rec.usage, outcome.usage),
        "error": {"message": outcome.error, "detail": None} if outcome.error else None,
    }
    if outcome.session is not None:                  # else keep what the handler checkpointed with save_session
        fields["session"] = outcome.session
    if outcome.status == "awaiting_input" and outcome.session is None:
        fields["status"] = "failed"
        fields["error"] = {"message": "handler returned awaiting_input without a session", "detail": None}
    if outcome.commit:
        branch = git.result_branch(rec.job_kind, rec.process, rec.id)
        checkout.push_branch(co, branch, outcome.commit)
        log.line(f"published {branch} at {outcome.commit[:12]}")
        fields |= {"result_branch": branch, "result_commit": outcome.commit}
    return fields


def _add_usage(a: JobUsage, b: JobUsage) -> JobUsage:
    """Totals over every attempt of the job."""
    by = dict(a.by)
    for key, usage in b.by.items():
        by[key] = by[key] + usage if key in by else usage
    return JobUsage(**(a + b).model_dump(), by=by)

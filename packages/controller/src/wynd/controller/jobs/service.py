"""`JobService` (PLAN §3.18, §8.1 "Job submit preconditions"; `$DRAFTS/06 §5.8`).

`submit` is the only place that resolves the current branch (`DetachedHead`); it requires a process that loads and
validates (`ValidationFailed`) and a clean workspace directory (`DirtyTree`). Commit-producing kinds use
`ref = HEAD` of the current branch and `inputs["target_branch"]` = that branch; `build`/`bake` use the closure HEAD.
With `WYND_GIT_REMOTE` set, the target branch is pushed before submitting. `answer` delegates to
`compile_view.apply_answers` and requeues the same job when the session is ready. `JobService` never imports the
compiler.

Submitted inputs by kind (`$DRAFTS/06 §5.8`): compile `{process, target_branch, answers, accept_proposals,
max_revisions?}`; test_live `{process, target_branch, steps?}`; build `{process, registry, push, platform?}`;
bake `{process}`; optimise whatever `submit_optimise` passes, plus `process` and `target_branch`.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from wynd.controller.errors import (
    Conflict,
    DetachedHead,
    DirtyTree,
    JobState,
    NotFound,
    Unavailable,
    ValidationFailed,
)
from wynd.controller.jobs import records
from wynd.controller.models import Issue, LogChunk, ValidationReportDTO
from wynd.process import git
from wynd.process.errors import LoadError, ProcessNotFound
from wynd.process.jobs import COMMIT_KINDS, TERMINAL, JobRecord
from wynd.process.validation import validate_process
from wynd.process.workspace import load_workspace

if TYPE_CHECKING:
    from wynd.controller.api.models_web import AnswerRequest
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import Job, JobKind
    from wynd.spec.errors import Diagnostic


class JobService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl

    def submit(self, kind: JobKind, pid: str, inputs: Mapping[str, Any] | None = None) -> Job:
        """Generic submit (also used by `wynd.controller.optimise.submit_optimise`); adds `process` and, for
        commit-producing kinds, `target_branch`."""
        root = self.ctx.root
        try:
            ws = load_workspace(root)
            diagnostics = validate_process(ws, pid).diagnostics
        except ProcessNotFound:
            raise NotFound(f"unknown process '{pid}'") from None
        except LoadError as err:
            raise _validation_failed(pid, err.diagnostics) from None
        if any(d.severity == "error" for d in diagnostics):
            raise _validation_failed(pid, diagnostics)
        dirty = git.dirty_paths(root, None)
        if dirty:
            raise DirtyTree(f"the workspace has uncommitted changes ({len(dirty)} path(s)); jobs run from commits only",
                            details={"paths": dirty}, hint="commit or stash them, then try again")
        payload = {**(inputs or {}), "process": pid}
        if kind in COMMIT_KINDS:
            branch = git.current_branch(root)
            if branch is None:
                raise DetachedHead(f"HEAD is detached; {kind} jobs integrate into the current branch",
                                   hint="check out a branch first")
            payload["target_branch"] = branch
            ref = git.rev_parse(root, "HEAD")
            remote = self.ctx.env.get("WYND_GIT_REMOTE")
            if remote:
                with self.ctx.git_lock:
                    git.git(root, "push", "--quiet", remote, f"refs/heads/{branch}:refs/heads/{branch}")
        else:
            ref = git.closure_head(root, git.reference_closure(ws, pid))
            if ref is None:
                raise Conflict(f"process '{pid}' has no commit yet", hint="commit it first")
        return self.get(self.ctx.runner.submit(kind, ref, payload))

    def submit_compile(
        self,
        pid: str,
        *,
        answers: Mapping[str, str] | None = None,
        accept_proposals: bool = False,
        max_revisions: int | None = None,
    ) -> Job:
        inputs: dict[str, Any] = {"answers": dict(answers or {}), "accept_proposals": accept_proposals}
        if max_revisions is not None:
            inputs["max_revisions"] = max_revisions
        return self.submit("compile", pid, inputs)

    def submit_test_live(self, pid: str, *, steps: Sequence[str] | None = None) -> Job:
        return self.submit("test_live", pid, {"steps": list(steps)} if steps else {})

    def submit_build(
        self, pid: str, *, registry: str | None = None, push: bool | None = None, platform: str | None = None
    ) -> Job:
        """`registry` defaults to the default image registry; `push` defaults to `registry is not None`."""
        if registry is None:
            registry = next((name for name, entry in self.ctx.stores.registry.list("registries").items()
                             if entry.get("default")), None)
        inputs: dict[str, Any] = {"registry": registry, "push": registry is not None if push is None else push}
        if platform is not None:
            inputs["platform"] = platform
        return self.submit("build", pid, inputs)

    def submit_bake(self, pid: str) -> Job:
        return self.submit("bake", pid, {})

    def answer(self, job_id: str, answers: Sequence[AnswerRequest] | Mapping[str, str]) -> Job:
        """Web `AnswerRequest`s or a question id -> answer text mapping (CLI). `JobState` unless the job is a
        `compile` job in `awaiting_input`."""
        from wynd.controller import compile_view

        job = self.ctx.runner.status(job_id)
        if job.job_kind != "compile" or job.status != "awaiting_input":
            raise JobState(f"job {job_id} is a {job.status} {job.job_kind} job; only a compile job awaiting input "
                           "takes answers")
        session, ready = compile_view.apply_answers(job, answers)
        pending = [q for q in session.get("questions", []) if q.get("status") == "pending"]
        records.update(self.ctx.stores.runs, job_id, session=session, questions=pending)
        if ready:
            given = _answer_texts(job, answers, compile_view.answer_text)
            answers_so_far = {**(job.inputs.get("answers") or {}), **given}
            self.ctx.runner.requeue(job_id, job.result_commit or job.ref,
                                    {**job.inputs, "answers": answers_so_far})
        return self.get(job_id)

    def get(self, job_id: str) -> Job:
        return records.to_dto(self.ctx.runner.status(job_id))

    def list(
        self,
        *,
        process_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        active: bool = False,
        limit: int = 50,
    ) -> list[Job]:
        """`active`: queued/running/awaiting_input, plus succeeded commit-producing jobs not yet integrated."""
        rows = self.ctx.stores.runs.list(kind="job", process=process_id, status=status, limit=sys.maxsize)
        jobs = []
        for row in rows:
            record = JobRecord.model_validate(row)
            if (kind is not None and record.job_kind != kind) or (active and not _active(record)):
                continue
            jobs.append(records.to_dto(record))
            if len(jobs) == limit:
                break
        return jobs

    def logs(self, job_id: str, offset: int = 0) -> LogChunk:
        text, next_offset, done = self.ctx.runner.logs(job_id, offset)
        return LogChunk(text=text, offset=next_offset, done=done)

    def cancel(self, job_id: str) -> Job:
        self.ctx.runner.cancel(job_id)
        return self.get(job_id)

    def wait(
        self,
        job_id: str,
        *,
        timeout: float | None = None,
        poll: float = 0.5,
        on_log: Callable[[str], None] | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> Job:
        """Returns on succeeded/failed/cancelled/awaiting_input; `Unavailable` on timeout (the job keeps running)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        offset, seen = 0, 0
        while True:
            record = self.ctx.runner.status(job_id)
            if on_log is not None:
                text, offset, _ = self.ctx.runner.logs(job_id, offset)
                if text:
                    on_log(text)
            if on_event is not None and record.session:
                for event in record.session.get("events", []):
                    if event.get("seq", 0) > seen:
                        on_event(event)
                        seen = event["seq"]
            if record.status in TERMINAL or record.status == "awaiting_input":
                return records.to_dto(record)
            if deadline is not None and time.monotonic() >= deadline:
                raise Unavailable(f"timed out waiting for job {job_id} (still {record.status}); it keeps running",
                                  hint=f"wynd jobs show {job_id}")
            time.sleep(poll)

    def integrate(self, job_id: str) -> Job:
        """Idempotent; `wynd.controller.jobs.integrate.integrate`."""
        from wynd.controller.jobs.integrate import integrate

        integrate(self.ctx, records.load(self.ctx.stores.runs, job_id))
        return self.get(job_id)


def _active(record: JobRecord) -> bool:
    if record.status in ("queued", "running", "awaiting_input"):
        return True
    return record.status == "succeeded" and record.job_kind in COMMIT_KINDS and record.integration is None


def _validation_failed(pid: str, diagnostics: Sequence[Diagnostic]) -> ValidationFailed:
    """`details` = the web `ValidationReportDTO` (PLAN §3.21 amendment 13)."""
    issues = [Issue.from_diagnostic(d) for d in diagnostics]
    report = ValidationReportDTO(ok=not any(d.severity == "error" for d in diagnostics), issues=issues)
    return ValidationFailed(f"process '{pid}' has validation errors; fix them before running a job",
                            details=report.model_dump(mode="json"), hint=f"wynd validate {pid}")


def _answer_texts(
    job: JobRecord, answers: Sequence[AnswerRequest] | Mapping[str, str], answer_text: Callable[[dict, Any], str]
) -> dict[str, str]:
    """The answers as `{question id: text}` (the cumulative `answers` input of the requeued job)."""
    if isinstance(answers, Mapping):
        return {str(qid): str(text) for qid, text in answers.items()}
    questions = {q.get("id"): q for q in (job.session or {}).get("questions", [])}
    return {a.question_id: answer_text(questions.get(a.question_id, {}), a) for a in answers}

"""`JobService` (PLAN §3.18, §8.1 "Job submit preconditions"; `$DRAFTS/06 §5.8`). Stub; CTL-JOBS.

`submit` is the only place that resolves the current branch (`DetachedHead`); it requires a process that loads and
validates (`ValidationFailed`) and a clean workspace directory (`DirtyTree`). Commit-producing kinds use
`ref = HEAD` of the current branch and `inputs["target_branch"]` = that branch; `build`/`bake` use the closure HEAD.
With `WYND_GIT_REMOTE` set, the target branch is pushed before submitting. `answer` delegates to
`compile_view.apply_answers` and requeues the same job when the session is ready. `JobService` never imports the
compiler.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.controller.api.models_web import AnswerRequest
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import Job, JobKind, LogChunk


class JobService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl

    def submit(self, kind: JobKind, pid: str, inputs: Mapping[str, Any] | None = None) -> Job:
        """Generic submit (also used by `wynd.controller.optimise.submit_optimise`); adds `process` and, for
        commit-producing kinds, `target_branch`."""
        raise NotImplementedError("PLAN §3.18")

    def submit_compile(
        self,
        pid: str,
        *,
        answers: Mapping[str, str] | None = None,
        accept_proposals: bool = False,
        max_revisions: int | None = None,
    ) -> Job:
        raise NotImplementedError("PLAN §3.18")

    def submit_test_live(self, pid: str, *, steps: Sequence[str] | None = None) -> Job:
        raise NotImplementedError("PLAN §3.18")

    def submit_build(
        self, pid: str, *, registry: str | None = None, push: bool | None = None, platform: str | None = None
    ) -> Job:
        """`registry` defaults to the default image registry; `push` defaults to `registry is not None`."""
        raise NotImplementedError("PLAN §3.18")

    def submit_bake(self, pid: str) -> Job:
        raise NotImplementedError("PLAN §3.18")

    def answer(self, job_id: str, answers: Sequence[AnswerRequest] | Mapping[str, str]) -> Job:
        """Web `AnswerRequest`s or a question id -> answer text mapping (CLI). `JobState` unless the job is a
        `compile` job in `awaiting_input`."""
        raise NotImplementedError("PLAN §3.18")

    def get(self, job_id: str) -> Job:
        raise NotImplementedError("PLAN §3.18")

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
        raise NotImplementedError("PLAN §3.18")

    def logs(self, job_id: str, offset: int = 0) -> LogChunk:
        raise NotImplementedError("PLAN §3.18")

    def cancel(self, job_id: str) -> Job:
        raise NotImplementedError("PLAN §3.18")

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
        raise NotImplementedError("PLAN §3.18")

    def integrate(self, job_id: str) -> Job:
        """Idempotent; `wynd.controller.jobs.integrate.integrate`."""
        raise NotImplementedError("PLAN §3.18")

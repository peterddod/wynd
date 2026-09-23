"""The `compile` job handler (`wynd.compiler.jobs:run_compile_job`; `$DRAFTS/05 §5`, PLAN §7 item 3, §3.18).

Resume is the same job requeued: the requeued attempt's checkout is at `job.result_commit` (the WIP commit),
`ctx.session` holds the session and `ctx.inputs["answers"]` is cumulative. `done` squashes onto `job.base_commit`
and returns `JobOutcome(commit=<sha>)`; the harness publishes `wynd/compile/<pid>/<job-id>`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.process.jobs import JobContext, JobOutcome


def run_compile_job(ctx: JobContext) -> JobOutcome:
    raise NotImplementedError("PLAN §7")

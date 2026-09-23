"""CTL-JOBS test job handlers, named `support.ctl_jobs_handlers:<function>` in runner handler tables (importable in
the test process and, with `PYTHONPATH=packages/controller/tests`, in subprocess workers).

Handlers read their knobs from `ctx.inputs`: `text` (file content), `gate` (a file whose existence releases
`wait_gate`), `path` (the workspace-relative file `commit_file` writes, default `processes/<pid>/NOTES.md`).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from pydantic import BaseModel

from wynd.process.jobs import JobContext, JobOutcome, JobUsage
from wynd.runtime.usage import Usage


def succeed(ctx: JobContext) -> JobOutcome:
    ctx.log(f"hello from {ctx.job.id}")
    ctx.log("line one\nline two")
    return JobOutcome(
        status="succeeded",
        artefacts={"answer": 42, "cwd_is_workspace": Path(ctx.workspace).is_dir()},
        report={"workspace": str(ctx.workspace), "worktree": str(ctx.worktree), "scratch": str(ctx.scratch),
                "workspace_root": str(ctx.workspace_root), "state_dir": str(ctx.state_dir),
                "attempt": ctx.job.attempt, "inputs": ctx.inputs},
        usage=JobUsage(input_tokens=10, output_tokens=5, calls=1, cost_usd=0.5,
                       by={"fake/cheap": Usage(input_tokens=10, output_tokens=5, calls=1, cost_usd=0.5)}),
    )


def commit_file(ctx: JobContext) -> JobOutcome:
    """Write one file (and delete `processes/<pid>/proto/<inputs["delete"]>.yaml` when asked) and commit with the
    default paths (the process's reference closure)."""
    pid = ctx.inputs["process"]
    rel = ctx.inputs.get("path") or f"processes/{pid}/NOTES.md"
    (ctx.workspace / rel).parent.mkdir(parents=True, exist_ok=True)
    (ctx.workspace / rel).write_text(ctx.inputs.get("text", f"written by {ctx.job.id}\n"))
    (ctx.workspace / "OUTSIDE.md").write_text("outside every closure\n")
    if ctx.inputs.get("delete"):
        (ctx.workspace / f"processes/{pid}/proto/{ctx.inputs['delete']}.yaml").unlink()
    sha = ctx.commit(f"notes for {pid}", None)
    ctx.log(f"committed {sha}")
    return JobOutcome(status="succeeded", commit=sha, artefacts={"committed": sha})


def fail(ctx: JobContext) -> JobOutcome:
    ctx.log("about to fail")
    (ctx.workspace / "half-done.txt").write_text("left for inspection\n")
    raise RuntimeError("boom")


def failed_outcome(ctx: JobContext) -> JobOutcome:
    return JobOutcome(status="failed", error="tests failed: extract")


def ask_then_finish(ctx: JobContext) -> JobOutcome:
    """First attempt: commit WIP and ask one question. Resumed attempt (ctx.session set): record what it got and
    finish with a commit."""
    pid = ctx.inputs["process"]
    if ctx.session is None:
        (ctx.workspace / f"processes/{pid}/WIP.md").write_text("work in progress\n")
        wip = ctx.commit("wip", None)
        question = {"id": "read.example1", "kind": "example_proposal", "status": "pending", "text": "Empty text?"}
        session = {"state": "awaiting_input", "questions": [question],
                   "events": [{"seq": 1, "type": "question", "text": "Empty text?"}]}
        ctx.save_session(session)
        return JobOutcome(status="awaiting_input", commit=wip, session=session, questions=[question],
                          usage=JobUsage(input_tokens=100, calls=1))
    (ctx.workspace / f"processes/{pid}/DONE.md").write_text(f"answers: {sorted(ctx.inputs.get('answers', {}))}\n")
    sha = ctx.commit("done", None)
    return JobOutcome(
        status="succeeded", commit=sha,
        report={"session_seen": ctx.session, "answers": ctx.inputs.get("answers"), "head_at_start": ctx.job.ref},
        session={**ctx.session, "state": "done"}, usage=JobUsage(input_tokens=1, calls=1),
    )


def checkpoint_only(ctx: JobContext) -> JobOutcome:
    """Checkpoint a session mid-run and return an outcome without one."""
    ctx.save_session({"state": "running", "checkpoint": 1})
    return JobOutcome(status="succeeded")


def awaiting_without_session(ctx: JobContext) -> JobOutcome:
    return JobOutcome(status="awaiting_input")


def wait_gate(ctx: JobContext) -> JobOutcome:
    """Run until the file `inputs["gate"]` exists (at most 60 s)."""
    gate = Path(ctx.inputs["gate"])
    ctx.log(f"waiting for {gate}")
    deadline = time.monotonic() + 60
    while not gate.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    return JobOutcome(status="succeeded")


def crash(ctx: JobContext) -> JobOutcome:
    """Kill the hosting process outright (a worker crash: nothing updates the record)."""
    ctx.log("crashing")
    os._exit(3)


class Prepared(BaseModel):
    commit: str
    context_dir: str


def prepare(ctx: JobContext) -> Prepared:
    ctx.log("preparing")
    context_dir = Path(os.environ.get("WYND_BUILD_CONTEXT_DIR") or ctx.scratch / "ctx")
    context_dir.mkdir(parents=True, exist_ok=True)
    (context_dir / "Dockerfile").write_text("FROM scratch\n")
    return Prepared(commit=ctx.job.ref, context_dir=str(context_dir))


def finalize(ctx: JobContext) -> JobOutcome:
    prepared = Prepared.model_validate(ctx.job.artefacts["prepared"])
    ctx.log("finalizing")
    dockerfile = Path(prepared.context_dir) / "Dockerfile"
    return JobOutcome(status="succeeded", artefacts={
        "commit": prepared.commit, "image": f"wynd/{ctx.job.process}:{prepared.commit[:12]}",
        "build_dir": prepared.context_dir, "pushed": False,
        "tests": {"passed": 3, "failed": 0, "total": 3, "source": "registry"},
        "dockerfile": dockerfile.read_text(),
    })

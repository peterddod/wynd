"""The `compile` job handler (`wynd.compiler.jobs:run_compile_job`; `$DRAFTS/05 §5`, PLAN §7 item 3, §3.18).

Resume is the same job requeued: the requeued attempt's checkout is at `job.result_commit` (the WIP commit),
`ctx.session` holds the session and `ctx.inputs["answers"]` is cumulative. `done` squashes onto `job.base_commit`
and returns `JobOutcome(commit=<sha>)`; the harness publishes `wynd/compile/<pid>/<job-id>`.

The handler owns the git ending of every pass, so it holds whatever the pipeline committed on the way:
- awaiting_input: a WIP commit of everything completed (`ctx.commit` returns None when the pipeline already made it);
- done / failed: `git reset --soft <base>` and one commit with `render_commit_message(report)`; a failed compile is
  still published for inspection; after a done commit, `run_tests(mode="replay")` records results at its closure
  HEAD so `wynd build` can reuse them (a failing replay fails the job).
The outcome's commit is the checkout's HEAD whenever that differs from the base commit, else None (nothing changed:
the harness publishes no branch).
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

from wynd.compiler.gitops import squash_to
from wynd.compiler.report import (CompileReport, ReportUsage, compiled_kind, render_commit_message, render_wip_message,
                                  summarize)
from wynd.compiler.session import CompileOptions, CompileSession, SessionState
from wynd.process.git import rev_parse
from wynd.process.jobs import JobOutcome, JobUsage
from wynd.runtime.usage import Usage
from wynd.spec.workspace import CASSETTES_DIR

if TYPE_CHECKING:
    from wynd.compiler.calls import CallKind
    from wynd.compiler.llm import CompilerLLM, LLMResult, Thinking, Tier
    from wynd.compiler.pipeline import CompileDeps
    from wynd.process.jobs import JobContext

T = TypeVar("T")
DEFAULT_COMPILER_PROVIDER = "claude-code"
MB = 1_000_000


def run_compile_job(ctx: JobContext) -> JobOutcome:
    from wynd.compiler.pipeline import CompileEnv

    pid = ctx.inputs["process"]
    base = ctx.job.base_commit
    if ctx.session:
        session = CompileSession.from_json(ctx.session)
    else:
        session = CompileSession.new(session_id=ctx.job.id, process=pid, base_commit=base,
                                     options=CompileOptions.from_inputs(ctx.inputs))
    session.begin_job(ctx.job.id)
    session.apply_answers(ctx.inputs.get("answers") or {}, accept_proposals=bool(ctx.inputs.get("accept_proposals")))
    report = session.data.report
    if session.state == SessionState.AWAITING_INPUT:
        ctx.log(f"{len(session.pending_questions)} question(s) still unanswered; not compiling")
        return _outcome(ctx, session, JobUsage())

    usage_before = report.usage.model_copy(deep=True)
    env = CompileEnv(
        checkout=ctx.workspace, worktree=ctx.worktree, scratch=ctx.scratch / "compile",
        deps=default_deps(ctx, session),
        checkpoint=lambda data: ctx.save_session(CompileSession(data).to_json()),
        commit=lambda message: ctx.commit(message, None), log=ctx.log,
    )
    ctx.log(f"compiling {pid} from {session.data.base_commit[:12]} (session {session.data.id})")
    state = session.next(env)
    report = session.data.report
    match state:
        case SessionState.AWAITING_INPUT:
            report.status = "awaiting_input"
            ctx.commit(render_wip_message(report), None)
        case SessionState.DONE:
            _finish(ctx, session, succeeded=True)
        case _:
            _finish(ctx, session, succeeded=False)
    return _outcome(ctx, session, attempt_usage(usage_before, report.usage, compiler_provider()))


def compiler_provider() -> str:
    return os.environ.get("WYND_COMPILER_PROVIDER") or DEFAULT_COMPILER_PROVIDER


def attempt_usage(before: ReportUsage, after: ReportUsage, provider: str) -> JobUsage:
    """This attempt's usage (the harness sums attempts): compiler calls keyed `<provider>/<tier>`, plus the steps'
    own model calls while recording under `recording`."""
    by = {}
    for tier, usage in after.by_tier.items():
        delta = _minus(usage, before.by_tier.get(tier, Usage()))
        if delta.calls:
            by[f"{provider}/{tier}"] = delta
    total = _minus(after.total, before.total)
    recording = _minus(after.recording, before.recording)
    if recording.calls:
        by["recording"] = recording
        total = total + recording
    return JobUsage(**total.model_dump(), by=by)


def default_deps(ctx: JobContext, session: CompileSession) -> CompileDeps:
    """The real boundaries (PLAN §7 item 6): the provider LLM (metered, behind the session memo), step venvs under
    `<state_dir>/venvs`, `uv pip compile` locking without wynd-spec/runtime, the process test runner, `describe` and
    MCP discovery."""
    from wynd.compiler.llm import MemoLLM, default_llm
    from wynd.compiler.pipeline import CompileDeps
    from wynd.process.bake import is_runtime
    from wynd.process.build.resolve import UvResolver
    from wynd.process.testing import run_process_examples, run_step_suite
    from wynd.process.venvs import describe, step_python
    from wynd.runtime.mcp import list_tools

    venv_root = ctx.state_dir / "venvs"
    llm = MeteredLLM(default_llm(ctx.registry, ctx.scratch / "llm"), session)

    def lock_requirements(requirements: Sequence[str]) -> list[str]:
        pins = UvResolver().compile(requirements, universal=True, python_version="3.12")
        return [pin for pin in pins if not is_runtime(pin)]

    return CompileDeps(
        llm=MemoLLM(llm, session.data.memo, on_usage=lambda kind, key, usage: None),   # metered inside the memo
        registry=ctx.registry,
        step_python=lambda requirements: step_python(requirements, venv_root=venv_root, log=ctx.log),
        lock_requirements=lock_requirements,
        run_step_suite=run_step_suite,
        run_process_examples=run_process_examples,
        describe=describe,
        list_mcp_tools=list_tools,
        now=lambda: datetime.now(UTC),
    )


class MeteredLLM:
    """A CompilerLLM that accounts every live call in the session (totals, per node, by kind and tier). It sits under
    the session memo, so memo hits cost nothing."""

    def __init__(self, inner: CompilerLLM, session: CompileSession):
        self.inner = inner
        self.session = session

    def call(self, kind: CallKind, *, node: str, system: str, prompt: str,
             response_model: type[T], tier: Tier, thinking: Thinking) -> LLMResult[T]:
        result = self.inner.call(kind, node=node, system=system, prompt=prompt, response_model=response_model,
                                 tier=tier, thinking=thinking)
        if not result.memo_hit:
            self.session.record_usage(kind=str(kind), node=node, tier=str(tier), usage=result.usage)
        return result


def _finish(ctx: JobContext, session: CompileSession, *, succeeded: bool) -> None:
    """Squash everything since the base into one commit; after a successful compile record replay results on it."""
    from wynd.process.workspace import load_workspace

    data = session.data
    report = data.report
    report.status = "succeeded" if succeeded else "failed"
    _cassette_warnings(ctx.workspace, report)
    if not report.summary:
        report.summary = summarize(report)
    if not succeeded and not report.error:
        report.error = "compile failed; see the report's steps and validation"
    squash_to(ctx.worktree, base=data.base_commit)
    sha = ctx.commit(render_commit_message(report), None)
    if sha is None:
        ctx.log("nothing changed; no commit")
        return
    session.emit("commit", f"Committed {sha[:12]} on {data.base_commit[:12]}.", process=data.process, step=None,
                 data={"commit": sha})
    if not succeeded:
        return
    replay = _record_tests(ctx, data.process, load_workspace(ctx.workspace))
    if replay.passed:
        return
    failing = [suite.subject for suite in replay.suites if not suite.passed]
    report.status = "failed"
    report.error = f"replay tests failed on the result commit: {', '.join(failing)}"
    data.state = SessionState.FAILED
    session.emit("error", report.error, process=data.process, step=None, data={"suites": failing})


def _record_tests(ctx: JobContext, pid: str, ws: Any) -> Any:
    """`run_tests(mode="replay")` at the closure HEAD of the new commit, recording results in `ctx.runs`."""
    from wynd.process import testing
    from wynd.process.git import closure_head, reference_closure

    head = closure_head(ws.root, reference_closure(ws, pid))
    ctx.log(f"replay tests at {head[:12] if head else 'HEAD'}")
    return testing.run_tests(ws, pid, mode="replay", commit=head, runs=ctx.runs, venv_root=ctx.state_dir / "venvs",
                             scratch=ctx.scratch / "compile" / "final", log=ctx.log)


def _cassette_warnings(workspace: Path, report: CompileReport) -> None:
    """SPEC §6.5: warn when one step's `cassettes/` exceeds `wynd.yaml` `cassette_warn_mb`."""
    from wynd.process.workspace import load_workspace

    limit = load_workspace(workspace).config.cassette_warn_mb
    for entry in report.steps:
        if entry.package is None:
            continue
        cassettes = workspace / entry.package / CASSETTES_DIR
        size = sum(p.stat().st_size for p in cassettes.rglob("*") if p.is_file()) if cassettes.is_dir() else 0
        entry.cassette_bytes = size
        warning = f"cassettes for {entry.step or entry.node} are {size / MB:.1f} MB (limit {limit:g} MB)"
        if size > limit * MB and warning not in report.warnings:
            report.warnings.append(warning)


def _outcome(ctx: JobContext, session: CompileSession, usage: JobUsage) -> JobOutcome:
    data = session.data
    report = data.report
    head = rev_parse(ctx.worktree, "HEAD")
    commit = head if head != data.base_commit else None
    if commit is not None:
        from wynd.process.git import result_branch

        report.commit, report.branch = commit, result_branch("compile", data.process, ctx.job.id)
    compiled = [s for s in report.steps if s.action == "compiled"]
    status = {SessionState.DONE: "succeeded", SessionState.AWAITING_INPUT: "awaiting_input"}.get(data.state, "failed")
    return JobOutcome(
        status=status,
        commit=commit,
        artefacts={
            "steps_compiled": len(compiled),
            "steps_skipped": sum(s.action == "skipped" for s in report.steps),
            "split": [s.node for s in compiled if compiled_kind(s) == "split"],
        },
        report=report.model_dump(mode="json", by_alias=True),
        session=session.to_json(),
        questions=[q.model_dump(mode="json") for q in session.pending_questions],
        error=report.error if status == "failed" else None,
        usage=usage,
    )


def _minus(a: Usage, b: Usage) -> Usage:
    cost = None if a.cost_usd is None else a.cost_usd - (b.cost_usd or 0.0)
    return Usage(input_tokens=a.input_tokens - b.input_tokens, output_tokens=a.output_tokens - b.output_tokens,
                 cache_read_tokens=a.cache_read_tokens - b.cache_read_tokens,
                 cache_write_tokens=a.cache_write_tokens - b.cache_write_tokens, cost_usd=cost,
                 latency_ms=a.latency_ms - b.latency_ms, calls=a.calls - b.calls)

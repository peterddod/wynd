"""M5 optimise: the report, `--apply` submission, the `optimise` job handler and its two API routes (PLAN §8.1
optimise row, §3.18, §15 item 55; `$DRAFTS/08 §3.7–§3.8` with `ctx.commit` instead of `ctx.git` and the tests run
through `wynd.process.testing`; owner OPT-CTL).

- `optimise_report` reads the newest `max_runs` runs of the process from the TraceSink (whatever started them) and
  builds `wynd.process.optimise.build_report` at the closure HEAD; `min_runs` is the R0 threshold.
- `submit_optimise` submits the report's applicable changes (only those of `units` when given) as an `optimise` job
  through `JobService.submit` (the process validates, the workspace is clean, a branch is checked out). With nothing
  left to apply it raises `NothingToApply` (409, `details` = the report).
- `run_optimise_job` re-reads each change's lock in the checkout (a tier that moved since the report is rejected),
  writes the new tier (canonical lock text, the file's leading comment block kept) and, for a step, runs the step's
  own tests live (record, promote, replay); a failure restores the lock and rejects the change. Branch changes have
  no tests of their own. When something was applied the root process's examples are re-recorded live; only if they
  pass is the result committed (the harness publishes `wynd/optimise/<pid>/<job id>`), and replay results are then
  recorded at the new closure HEAD for the build gate (`artefacts["replay"]`; as for `test_live`, they do not decide
  the job: the changed step and the examples were already replayed after recording). Nothing applied: succeeded
  without a commit. Failing process examples: failed without a commit. The job report is `{branch, commit,
  report_commit, applied, rejected, process_tests, usage}`; `usage` counts the live model calls of the job's tests.

`render_report_text` is re-exported for the CLI, which imports only `wynd.controller`. `router` is mounted by
`api/app.py` before the catch-all process routes.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from pydantic import BaseModel

from wynd.controller.api.app import Ctl
from wynd.controller.errors import Conflict, Invalid, NotFound, translated
from wynd.process.jobs import JobOutcome, JobUsage
from wynd.process.optimise import (
    RULES_V1,
    OptimiseJobInput,
    OptimiseReport,
    TierChange,
    build_report,
    collect_stats,
    edges_lock_of,
    render_report_text,
)
from wynd.process.workspace import join, load_workspace
from wynd.runtime.cassettes import promote
from wynd.spec.base import DEFAULT_TIER
from wynd.spec.lockfiles import dump_lock
from wynd.spec.workspace import CASSETTES_DIR, EDGES_LOCK_FILE, STEP_LOCK_FILE

if TYPE_CHECKING:
    from wynd.controller.controller import Controller
    from wynd.process.jobs import JobContext
    from wynd.process.testing import SuiteResult
    from wynd.process.workspace import Workspace

__all__ = ["NothingToApply", "optimise_report", "render_report_text", "router", "run_optimise_job",
           "submit_optimise"]

PROCESS = "/api/processes/{process_id:path}"
EVENTS_FILE = "events.jsonl"

router = APIRouter()


class NothingToApply(Conflict):
    """No applicable tier change is left to submit; `details` = the report (JSON)."""

    code = "nothing_to_apply"


class OptimiseRequest(BaseModel):
    units: list[str] | None = None
    max_runs: int = 200
    min_runs: int = 20


def optimise_report(ctl: Controller, process_id: str, *, max_runs: int = 200, min_runs: int = 20) -> OptimiseReport:
    """The report over the newest `max_runs` runs of the process at its closure HEAD ("" before its first commit)."""
    from wynd.process.git import closure_head, reference_closure

    if max_runs < 1 or min_runs < 1:
        raise Invalid(f"max_runs and min_runs must be at least 1 (got {max_runs} and {min_runs})")
    ctx = ctl.ctx
    ws = ctx.workspace()
    if process_id not in ws.processes:
        raise NotFound(f"unknown process '{process_id}'")
    with translated():
        lp = ws.load_process(process_id)
        commit = closure_head(ctx.root, reference_closure(ws, process_id)) or ""
    runs = ctx.stores.runs.list(kind="run", process=process_id, limit=max_runs)     # newest first
    stats = collect_stats(process_id, [run["id"] for run in reversed(runs)], ctx.stores.traces.read)
    report = build_report(lp, edges_lock_of(lp), stats, commit=commit, rules=replace(RULES_V1, min_samples=min_runs),
                          now=ctx.clock())
    report.window["max_runs"] = max_runs
    return report


def submit_optimise(
    ctl: Controller,
    process_id: str,
    *,
    units: list[str] | None = None,
    max_runs: int = 200,
    min_runs: int = 20,
) -> tuple[str, OptimiseReport]:
    """-> (job_id, the report with `changes` = the submitted ones); `ctl.jobs.submit("optimise", ...)`."""
    report = optimise_report(ctl, process_id, max_runs=max_runs, min_runs=min_runs)
    changes = _selected(report, units)
    report = report.model_copy(update={"changes": changes})
    if not changes:
        scope = f" for {', '.join(units)}" if units else ""
        raise NothingToApply(f"nothing to apply: no applicable tier change{scope} in {process_id}",
                             details=report.model_dump(mode="json"), hint=f"see why: wynd optimise {process_id}")
    inputs = {"report_commit": report.commit, "changes": [change.model_dump(mode="json") for change in changes]}
    job = ctl.jobs.submit("optimise", process_id, inputs)
    return job.id, report


def _selected(report: OptimiseReport, units: Sequence[str] | None) -> list[TierChange]:
    if units is None:
        return list(report.changes)
    known = [unit.unit for unit in report.units]
    unknown = [unit for unit in units if unit not in known]
    if unknown:
        raise Invalid(f"unknown unit(s) of {report.process}: {', '.join(unknown)}", details={"units": known})
    return [change for change in report.changes if change.unit in units]


# --- the job ---------------------------------------------------------------------------------------------------------

def run_optimise_job(ctx: JobContext) -> JobOutcome:
    """The `optimise` job handler (`DEFAULT_HANDLERS["optimise"]`)."""
    from wynd.process.git import result_branch

    inp = OptimiseJobInput.model_validate(ctx.inputs)
    pid = inp.process
    events = ctx.scratch / EVENTS_FILE
    env = {**os.environ, "WYND_EVENTS_FILE": str(events)}
    applied: list[tuple[TierChange, dict[str, int] | None]] = []
    rejected: list[dict[str, Any]] = []
    for change in inp.changes:
        ctx.log(f"{change.unit}: {change.from_tier} -> {change.to_tier} ({change.rule})")
        ws = load_workspace(ctx.workspace)
        if change.lock_key is None:
            reason, tests = _apply_step(ctx, ws, pid, change, env)
        else:
            reason, tests = _apply_edge(ws, pid, change), None
        if reason is None:
            applied.append((change, tests))
            continue
        ctx.log(f"rejected {change.unit}: {reason}")
        rejected.append({"unit": change.unit, "from_tier": change.from_tier, "to_tier": change.to_tier,
                         "reason": reason})

    result: dict[str, Any] = {
        "branch": None, "commit": None, "report_commit": inp.report_commit,
        "applied": [{"unit": c.unit, "from_tier": c.from_tier, "to_tier": c.to_tier, "rule": c.rule, "tests": t}
                    for c, t in applied],
        "rejected": rejected, "process_tests": None,
    }
    if not applied:
        ctx.log("nothing applied")
        return _outcome("succeeded", result, events)

    suite = _live_process_suite(ctx, load_workspace(ctx.workspace), pid, env)
    result["process_tests"] = _counts(suite)
    if not suite.passed:
        return _outcome("failed", result, events,
                        error=f"process examples failed live after the tier changes ({_failures(suite)}); "
                              "nothing committed")
    sha = ctx.commit(_commit_message(inp, [c for c, _ in applied], rejected), None)
    if sha is None:
        return _outcome("succeeded", result, events)
    result |= {"branch": result_branch("optimise", pid, ctx.job.id), "commit": sha}
    replay = _record_results(ctx, pid)
    if not replay["passed"]:
        ctx.log(f"replay at {sha[:7]}: some suites fail; the build gate will refuse this commit (wynd test {pid})")
    return _outcome("succeeded", result, events, commit=sha, artefacts={"replay": replay})


def _apply_step(
    ctx: JobContext, ws: Workspace, pid: str, change: TierChange, env: Mapping[str, str]
) -> tuple[str | None, dict[str, int] | None]:
    """-> (why the change is rejected, None) or (None, the step's live test counts)."""
    rs = ws.load_process(pid).steps.get(change.unit)
    pkg = rs.package if rs is not None and rs.ref_kind == "local" else None
    if pkg is None or pkg.lock is None or join(pkg.dir, STEP_LOCK_FILE) != change.lock_path:
        return f"{change.lock_path} is not the lock of a process-local step {change.unit} of {pid}", None
    if pkg.lock.kind != "agentic":
        return f"{change.unit} is a {pkg.lock.kind} step, not an agentic one", None
    current = pkg.lock.tier or DEFAULT_TIER
    if current != change.from_tier:
        return f"lock is now {current}, report assumed {change.from_tier}", None
    path = ws.root / change.lock_path
    before = _write_lock(path, pkg.lock.model_copy(update={"tier": change.to_tier}))
    ctx.log(f"{change.unit}: step tests, live at {change.to_tier}")
    scratch = ctx.scratch / "steps" / change.unit
    suite = _live_step_suite(ctx, load_workspace(ctx.workspace), pid, pkg.id, env, scratch)
    if not suite.passed:
        path.write_text(before, encoding="utf-8")
        return f"tests failed live at {change.to_tier}: {_failures(suite)}", None
    return None, _counts(suite)


def _apply_edge(ws: Workspace, pid: str, change: TierChange) -> str | None:
    """-> why the change is rejected, or None once the root's edges.lock.yaml holds the new tier."""
    lp = ws.load_process(pid)
    path = join(lp.dir, EDGES_LOCK_FILE)
    lock = edges_lock_of(lp)
    entry = lock.edges.get(change.lock_key)
    if change.lock_path != path or change.unit != f"edge:{change.lock_key}" or entry is None:
        return f"{change.lock_key} is not a branch locked in {path}"
    if entry.tier != change.from_tier:
        return f"lock is now {entry.tier}, report assumed {change.from_tier}"
    edges = {**lock.edges, change.lock_key: entry.model_copy(update={"tier": change.to_tier})}
    _write_lock(ws.root / path, lock.model_copy(update={"edges": edges}))
    return None


def _write_lock(path: Path, lock: BaseModel) -> str:
    """Write the lock's canonical text after the file's leading comment block; -> the previous text."""
    before = path.read_text(encoding="utf-8")
    header = "".join(itertools.takewhile(lambda line: line.startswith("#"), before.splitlines(keepends=True)))
    path.write_text(header + dump_lock(lock), encoding="utf-8")
    return before


# The three test runs below are the job's only calls into the test runner (tests replace them). The two live ones
# use the public runner (`run_step_suite`, `run_process_examples`) with PLAN §3.20's live-mode steps.

def _live_step_suite(
    ctx: JobContext, ws: Workspace, pid: str, step_id: str, env: Mapping[str, str], scratch: Path
) -> SuiteResult:
    """The process-local step's own suite in live mode, in its plan venv with the §3.16 literals (`<process>`,
    `<ws>`) and the root's provider."""
    from wynd.process.plan import plan_local
    from wynd.process.testing import run_step_suite

    plan = plan_local(ws, pid, venv_root=ctx.state_dir / "venvs", log=ctx.log)
    step = plan.steps[step_id]
    venv = next(v for v in plan.venvs if v.id == step.venv)
    python = Path(venv.python) if venv.python else Path(plan.venv_root) / venv.id / "bin" / "python"
    pkg_dir = Path(step.package_dir)
    literals = _literals({Path(plan.processes[pid].dir): "<process>", ws.root: "<ws>"})
    suite_env = {**env, "WYND_DEFAULT_PROVIDER": plan.provider, "WYND_CASSETTE_LITERALS": json.dumps(literals)}

    def run(mode: str, record_dir: Path | None = None) -> SuiteResult:
        out = scratch / mode
        shutil.rmtree(out, ignore_errors=True)
        return run_step_suite(pkg_dir, python=python, mode=mode, env=suite_env, junit=out / "junit.xml",
                              basetemp=out / "tmp", record_dir=record_dir)

    return _record_promote_replay(scratch, pkg_dir / CASSETTES_DIR, run)


def _live_process_suite(ctx: JobContext, ws: Workspace, pid: str, env: Mapping[str, str]) -> SuiteResult:
    """The root process's examples in live mode; recordings are promoted into `<process dir>/cassettes/`."""
    from wynd.process.testing import run_process_examples

    ctx.log(f"process examples of {pid}, live")
    scratch = ctx.scratch / "process"

    def run(mode: str, record_root: Path | None = None) -> SuiteResult:
        return run_process_examples(ws, pid, mode=mode, env=env, scratch=scratch / mode,
                                    venv_root=ctx.state_dir / "venvs", record_root=record_root, log=ctx.log)

    return _record_promote_replay(scratch, ws.process_dir(pid) / CASSETTES_DIR, run)


def _record_promote_replay(
    scratch: Path, dest: Path, run: Callable[[str, Path | None], SuiteResult]
) -> SuiteResult:
    """`run("record", <scratch>/recorded)`; only if that passed, promote the recordings into `dest` and
    `run("replay", None)` (the cassette check), putting the previous recordings back when the replay fails."""
    staging, previous = scratch / "recorded", scratch / "previous"
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(previous, ignore_errors=True)
    recorded = run("record", staging)
    if not recorded.passed:
        return recorded
    if dest.is_dir():
        shutil.copytree(dest, previous)
    promote(staging, dest)
    replayed = run("replay", None)
    if not replayed.passed:
        promote(previous, dest)
    return replayed


def _literals(paths: Mapping[Path, str]) -> dict[str, str]:
    """Normaliser literals for each path and its realpath (`/var` vs `/private/var`)."""
    return {form: placeholder for path, placeholder in paths.items() for form in (str(path), os.path.realpath(path))}


def _record_results(ctx: JobContext, pid: str) -> dict[str, Any]:
    """Replay every suite at the new closure HEAD and record the results (what `wynd build` checks)."""
    from wynd.process.git import closure_head, reference_closure
    from wynd.process.testing import run_tests

    ws = load_workspace(ctx.workspace)
    head = closure_head(ws.root, reference_closure(ws, pid))
    report = run_tests(ws, pid, mode="replay", commit=head, runs=ctx.runs, venv_root=ctx.state_dir / "venvs",
                       scratch=ctx.scratch / "replay", log=ctx.log)
    return {"commit": head, "passed": report.passed, "recorded": report.recorded}


def _outcome(
    status: str, result: dict[str, Any], events: Path, *, commit: str | None = None,
    artefacts: dict[str, Any] | None = None, error: str | None = None,
) -> JobOutcome:
    usage = _usage(events)
    return JobOutcome(status=status, commit=commit, report={**result, "usage": usage.model_dump(mode="json")},
                      artefacts=artefacts or {}, usage=usage, error=error)


def _usage(events: Path) -> JobUsage:
    """The live (recorded, not replayed) `model.call`s of the job's test runs: totals and per `<provider>/<tier>`."""
    total, by = JobUsage(), {}
    lines = events.read_text(encoding="utf-8").splitlines() if events.is_file() else []
    for line in lines:
        event = json.loads(line)
        if event.get("type") != "model.call" or event.get("cassette") == "replay" or not event.get("usage"):
            continue
        call = JobUsage.model_validate({**event["usage"], "calls": 1})
        key = f"{event.get('provider')}/{event.get('tier')}"
        total = total + call
        by[key] = by.get(key, JobUsage()) + call
    return JobUsage(**total.model_dump(exclude={"by"}), by={key: u.model_dump() for key, u in by.items()})


def _counts(suite: SuiteResult) -> dict[str, int]:
    return {"passed": suite.counts.get("passed", 0),
            "failed": suite.counts.get("failed", 0) + suite.counts.get("error", 0)}


def _failures(suite: SuiteResult) -> str:
    """`<failed>/<total> (<first three failing cases>)`, or the suite's problem when no case failed."""
    failing = [case.name for case in suite.cases if case.outcome in ("failed", "error")]
    if not failing and suite.problem:
        return suite.problem.splitlines()[0]
    names = f" ({', '.join(failing[:3])})" if failing else ""
    return f"{len(failing)}/{sum(suite.counts.values())}{names}"


def _commit_message(inp: OptimiseJobInput, applied: Sequence[TierChange], rejected: Sequence[dict[str, Any]]) -> str:
    """`$DRAFTS/08 §3.7`'s message. `ctx.commit` appends `Wynd-Job: <id>` as a paragraph of its own, so the
    `Wynd-Report-Commit` line is body text, not a git trailer (the job report carries `report_commit`)."""
    lines = [f"wynd optimise: {inp.process}", ""]
    lines += [f"{c.unit}: {c.from_tier} -> {c.to_tier} ({c.rule}: {_evidence(c.evidence)})" for c in applied]
    lines += [f"rejected {r['unit']}: {r['from_tier']} -> {r['to_tier']} ({r['reason']})" for r in rejected]
    if inp.report_commit:
        lines += ["", f"Wynd-Report-Commit: {inp.report_commit[:12]}"]
    return "\n".join(lines)


def _evidence(evidence: Mapping[str, float]) -> str:
    n = int(evidence.get("n", 0))
    return (f"{round(evidence.get('fail_rate', 0.0) * n)} failures, {evidence.get('vfail_rate', 0.0):.1%} validation "
            f"failures, {evidence.get('mean_attempts', 0.0):.2f} attempts over {n} executions")


# --- API routes (PLAN §3.21: mounted before the catch-all process routes) ---------------------------------------------

@router.get(f"{PROCESS}/optimise")
def get_optimise(process_id: str, ctl: Ctl, max_runs: int = 200, min_runs: int = 20):
    return optimise_report(ctl, process_id, max_runs=max_runs, min_runs=min_runs)


@router.post(f"{PROCESS}/optimise", status_code=201)
def post_optimise(process_id: str, ctl: Ctl, body: OptimiseRequest | None = None):
    body = body or OptimiseRequest()
    job_id, report = submit_optimise(ctl, process_id, units=body.units, max_runs=body.max_runs,
                                     min_runs=body.min_runs)
    return {"job_id": job_id, "changes": [change.model_dump(mode="json") for change in report.changes]}

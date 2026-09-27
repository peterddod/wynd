"""The test runner: step suites, process examples and test records (PLAN §3.20; owner PROC-ENV).

`run_tests` = `$DRAFTS/04 §7` (plan, per-package interface-drift check via `python -m wynd.runtime.describe`, hermetic
pytest per compiled package in its venv, exit code 5 = "no tests collected" = failure) + the process examples of
every closure process (root first). Results are recorded only when `commit` and `runs` are given and the closure is
clean. The result models below are the W0-complete contract.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from wynd.runtime.cassettes import NO_RECORDING, promote
from wynd.runtime.errors import InvalidProcessInputs
from wynd.runtime.storage import stores_from_env
from wynd.runtime.storage.models import TestResult
from wynd.runtime.testing import match_outputs, sub_tmp
from wynd.spec.base import RESERVED_EXIT
from wynd.spec.hashing import interface_hash
from wynd.spec.interface import Interface
from wynd.spec.typelang import TScalar
from wynd.spec.workspace import CASSETTES_DIR, slug, step_module_name

from .hashing import process_hash, step_hash
from .local import run_plan
from .plan import plan_local
from .venvs import describe

if TYPE_CHECKING:
    from wynd.runtime.storage.base import RunRegistry
    from wynd.spec.lockfiles import StepLock
    from wynd.spec.plan import PlanStep, RunPlan
    from wynd.spec.proto_step import Example

    from .jobs import JobContext, JobOutcome
    from .loader import StepPackage
    from .workspace import Workspace

COUNT_KEYS = ("passed", "failed", "error", "skipped")
PYTEST_INI = "[pytest]\naddopts = -p no:cacheprovider --import-mode=importlib\n"
NO_TESTS = "no tests collected"


class TestCase(BaseModel):
    __test__ = False                      # not a pytest test class
    name: str
    outcome: Literal["passed", "failed", "error", "skipped"]
    message: str | None = None
    duration_ms: int


class SuiteResult(BaseModel):
    subject: str
    hash: str
    passed: bool
    counts: dict[str, int]
    cases: list[TestCase]
    problem: str | None = None


class TestReport(BaseModel):
    __test__ = False                      # not a pytest test class
    process: str
    commit: str | None
    process_hash: str
    mode: Literal["replay", "live"]
    passed: bool
    suites: list[SuiteResult]
    started_at: datetime
    finished_at: datetime
    recorded: bool = False


def run_tests(
    ws: Workspace,
    pid: str,
    *,
    mode: Literal["replay", "live"],
    commit: str | None,
    runs: RunRegistry | None,
    venv_root: Path,
    scratch: Path,
    env: Mapping[str, str] | None = None,
    log: Callable[[str], None] | None = None,
) -> TestReport:
    """Every compiled step package's suite (sorted by id), then the examples of every closure process (root first).

    Live mode records agentic step suites and process examples into staging dirs under `scratch`, promotes a
    suite's recordings into the tree only when it passed, then re-runs it in replay (the cassette check; the old
    recordings are restored if that fails)."""
    started = _now()
    base_env = dict(os.environ if env is None else env)
    lp = ws.load_process(pid)
    plan = plan_local(ws, pid, venv_root=venv_root, log=log)
    packages = lp.closure_packages()
    hashes = {sid: step_hash(ws.tree, packages[sid]) for sid in plan.steps}
    root_hash = process_hash(ws.tree, lp)
    suites: list[SuiteResult] = []
    for sid, step in sorted(plan.steps.items()):
        if log is not None:
            log(f"step suite {sid}")
        suite = _step_suite(ws, pid, plan, step, packages[sid], mode, base_env, Path(scratch) / "steps" /
                            step_module_name(sid))
        suites.append(suite.model_copy(update={"subject": sid, "hash": hashes[sid]}))
    for cid in lp.closure_processes():
        if log is not None:
            log(f"process examples {cid}")
        suites.append(_process_suite(ws, cid, mode, base_env, Path(scratch) / "processes" / slug(cid), venv_root,
                                     log))
    recorded = commit is not None and runs is not None and _closure_clean(ws, pid)
    report = TestReport(
        process=pid, commit=commit, process_hash=root_hash, mode=mode, passed=all(s.passed for s in suites),
        suites=suites, started_at=started, finished_at=_now(), recorded=recorded,
    )
    if recorded:
        _record(runs, commit, pid, report)
    return report


def run_step_suite(
    pkg_dir: Path,
    *,
    python: Path,
    mode: Literal["replay", "record"],
    env: Mapping[str, str],
    junit: Path,
    basetemp: Path,
    record_dir: Path | None = None,
    timeout_s: int = 1800,
) -> SuiteResult:
    """pytest over one step package in its venv, hermetically: only `<junit dir>/pytest.ini` applies (no conftest or
    config above the package), no cache, no bytecode. `subject` is the package dir and `hash` is empty (callers that
    know the step id and hash fill them in); `TestCase.message` carries the full failure text."""
    pkg_dir = Path(pkg_dir).absolute()
    junit, basetemp = Path(junit), Path(basetemp)
    junit.parent.mkdir(parents=True, exist_ok=True)
    junit.unlink(missing_ok=True)
    ini = junit.parent / "pytest.ini"
    ini.write_text(PYTEST_INI)
    run_env = {**env, "PYTHONDONTWRITEBYTECODE": "1", "WYND_CASSETTE_MODE": mode}
    run_env.pop("WYND_CASSETTE_RECORD_DIR", None)
    if record_dir is not None:
        run_env["WYND_CASSETTE_RECORD_DIR"] = str(record_dir)
    args = [str(python), "-m", "pytest", "-q", "-c", str(ini), "--rootdir", str(pkg_dir), "--confcutdir",
            str(pkg_dir), "--basetemp", str(basetemp), "--junitxml", str(junit), str(pkg_dir)]
    try:
        proc = subprocess.run(args, cwd=pkg_dir, env=run_env, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return _suite(str(pkg_dir), "", _junit_cases(junit), problem=f"pytest timed out after {timeout_s}s")
    cases = _junit_cases(junit)
    match proc.returncode:
        case 0 | 1:
            problem = None
        case 5:
            problem = NO_TESTS
        case code:
            problem = f"pytest exited with code {code}:\n{_tail(proc.stdout + proc.stderr)}"
    return _suite(str(pkg_dir), "", cases, problem=problem, passed=proc.returncode == 0)


def run_process_examples(
    ws: Workspace,
    pid: str,
    *,
    mode: Literal["replay", "record"],
    env: Mapping[str, str],
    scratch: Path,
    venv_root: Path,
    record_root: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> SuiteResult:
    """Run each `process.yaml` example through the local executor: relative `path` inputs resolve against the process
    dir, `{tmp}` is a fresh dir per example, the example's `env:` is merged over `env`, `run_id = run-example-<n>`,
    cassettes at `<process dir>/cassettes/<step_module_name>/` (recording into `record_root`, default the same dir).

    A case passes iff the exit matches and `match_outputs` finds nothing, except that any cassette miss in the run
    (a `step.end` with cause `cassette_miss`, or an `edge.check` with error cause `cassette_miss`) makes it an
    `error` with the miss text, whatever the final exit."""
    lp = ws.load_process(pid)
    plan = plan_local(ws, pid, venv_root=venv_root, log=log)
    process_dir = ws.process_dir(pid)
    cassette_root = process_dir / CASSETTES_DIR
    if mode == "record" and record_root is None:
        record_root = cassette_root
    cases = [
        _example_case(ws, plan, lp.doc.inputs, process_dir, n, example, mode, dict(env), Path(scratch) / f"example-{n}",
                      cassette_root, record_root if mode == "record" else None)
        for n, example in enumerate(lp.doc.examples, start=1)
    ]
    return _suite(f"process:{pid}", process_hash(ws.tree, lp), cases)


def tests_status(runs: RunRegistry, ws: Workspace, pid: str, commit: str) -> dict[str, Any]:
    """`{"status": "passed"|"failed"|"missing", "steps": {step id: same}}` from the results recorded for `commit`,
    keyed by the hashes of `ws`'s tree (pass a `CommitTree` workspace for the status at a commit)."""
    lp = ws.load_process(pid)
    whole = runs.get_test_result(commit, f"process:{pid}:{process_hash(ws.tree, lp)}")
    steps = {
        sid: _status(runs.get_test_result(commit, f"step:{sid}:{step_hash(ws.tree, pkg)}"))
        for sid, pkg in sorted(lp.closure_packages().items())
    }
    return {"status": _status(whole), "steps": steps}


def run_test_live_job(ctx: JobContext) -> JobOutcome:
    """`test_live` handler: `run_tests(mode="live")` in the checkout; commit the re-recorded cassettes (only suites
    that passed were promoted); then `run_tests(mode="replay")` recording results at the new closure HEAD. Succeeds
    iff every live suite passed; the branch is published either way."""
    from .git import closure_head, reference_closure
    from .jobs import JobOutcome
    from .workspace import load_workspace

    pid = ctx.inputs["process"]
    venv_root = ctx.state_dir / "venvs"
    live = run_tests(load_workspace(ctx.workspace), pid, mode="live", commit=None, runs=None, venv_root=venv_root,
                     scratch=ctx.scratch / "live", log=ctx.log)
    sha = ctx.commit(f"wynd test --live: re-record cassettes for {pid}", None)
    ws = load_workspace(ctx.workspace)                          # a fresh tree: the cassettes changed
    head = closure_head(ws.root, reference_closure(ws, pid))
    replay = run_tests(ws, pid, mode="replay", commit=head, runs=ctx.runs, venv_root=venv_root,
                       scratch=ctx.scratch / "replay", log=ctx.log)
    failing = [suite.subject for suite in live.suites if not suite.passed]
    return JobOutcome(
        status="succeeded" if live.passed else "failed",
        commit=sha,
        report=live.model_dump(mode="json"),
        artefacts={"replay": {"commit": head, "passed": replay.passed, "recorded": replay.recorded}},
        error=f"live tests failed: {', '.join(failing)}" if failing else None,
    )


# --- step suites ----------------------------------------------------------------------------------------------------

def _step_suite(
    ws: Workspace, pid: str, plan: RunPlan, step: PlanStep, pkg: StepPackage, mode: Literal["replay", "live"],
    env: dict[str, str], scratch: Path,
) -> SuiteResult:
    pkg_dir = ws.root / pkg.dir
    python = Path(plan.venv_root) / step.venv / "bin" / "python"
    problem = _drift(python, pkg_dir, step.lock, pid)
    if problem is not None:
        return _suite(step.id, "", [], problem=problem)
    process_dir = ws.process_dir(step.id.rpartition("#")[0]) if "#" in step.id else pkg_dir
    literals = _literals({process_dir: "<process>", ws.root: "<ws>"})
    suite_env = {**env, "WYND_DEFAULT_PROVIDER": plan.provider, "WYND_CASSETTE_LITERALS": json.dumps(literals)}

    def run(suite_mode: Literal["replay", "record"], record_dir: Path | None) -> SuiteResult:
        out = scratch / suite_mode
        shutil.rmtree(out, ignore_errors=True)
        return run_step_suite(pkg_dir, python=python, mode=suite_mode, env=suite_env, junit=out / "junit.xml",
                              basetemp=out / "tmp", record_dir=record_dir)

    if mode == "replay" or step.kind != "agentic":
        return run("replay", None)
    staging = scratch / "recorded"
    shutil.rmtree(staging, ignore_errors=True)
    recorded = run("record", staging)
    if not recorded.passed:
        return recorded
    return _promote_checked(staging, pkg_dir / CASSETTES_DIR, scratch / "previous", lambda: run("replay", None))


def _drift(python: Path, pkg_dir: Path, lock: StepLock, pid: str) -> str | None:
    """The lock snapshots the worker verifies at `init` (PLAN §3.6), compared with the code; None when they agree."""
    try:
        described = describe(python, pkg_dir, lock.entrypoint)
    except RuntimeError as err:
        return str(err)
    fields = []
    if lock.interface is not None:
        if interface_hash(lock.interface) != interface_hash(Interface.model_validate(described["interface"])):
            fields.append("interface")
    if described["kind"] != lock.kind:
        fields.append("kind")
    if lock.kind == "agentic" and list(lock.context) != list(described.get("context") or []):
        fields.append("context")
    if lock.kind == "shell" and lock.shell is not None:
        if {str(code): exit for code, exit in lock.shell.exit_codes.items()} != described.get("exit_codes"):
            fields.append("shell.exit_codes")
    if not fields:
        return None
    return (f"interface snapshot drift: step.lock.yaml {', '.join(fields)} differs from {lock.entrypoint}; "
            f"run wynd validate {pid} --sync-interfaces")


def _junit_cases(path: Path) -> list[TestCase]:
    if not path.is_file():
        return []
    cases = []
    for case in ET.parse(path).getroot().iter("testcase"):
        outcome, message = "passed", None
        for tag, name in (("failure", "failed"), ("error", "error"), ("skipped", "skipped")):
            found = case.find(tag)
            if found is not None:
                outcome, message = name, (found.text or found.get("message") or "").strip() or None
                break
        cases.append(TestCase(name=case.get("name", ""), outcome=outcome, message=message,
                              duration_ms=int(float(case.get("time") or 0) * 1000)))
    return cases


# --- process examples -----------------------------------------------------------------------------------------------

def _process_suite(
    ws: Workspace, pid: str, mode: Literal["replay", "live"], env: dict[str, str], scratch: Path, venv_root: Path,
    log: Callable[[str], None] | None,
) -> SuiteResult:
    def run(suite_mode: Literal["replay", "record"], record_root: Path | None = None) -> SuiteResult:
        return run_process_examples(ws, pid, mode=suite_mode, env=env, scratch=scratch / suite_mode,
                                    venv_root=venv_root, record_root=record_root, log=log)

    if mode == "replay":
        return run("replay")
    staging = scratch / "recorded"
    shutil.rmtree(staging, ignore_errors=True)
    recorded = run("record", staging)
    if not recorded.passed:
        return recorded
    return _promote_checked(staging, ws.process_dir(pid) / CASSETTES_DIR, scratch / "previous",
                            lambda: run("replay"))


def _example_case(
    ws: Workspace, plan: RunPlan, input_types: Mapping[str, Any], process_dir: Path, n: int, example: Example,
    mode: Literal["replay", "record"], env: dict[str, str], scratch: Path, cassette_root: Path,
    record_root: Path | None,
) -> TestCase:
    shutil.rmtree(scratch, ignore_errors=True)
    tmp = scratch / "tmp"
    tmp.mkdir(parents=True)
    inputs = _resolve_paths(sub_tmp(example.inputs, tmp), input_types, process_dir)
    run_env = {**env, "PYTHONDONTWRITEBYTECODE": "1", **sub_tmp(example.env, tmp)}   # nothing lands in the tree
    stores = stores_from_env({**run_env, "WYND_DATA_DIR": str(scratch / "data"), "WYND_WORKSPACE_STORE": "file",
                              "WYND_TRACE_SINK": "jsonl", "WYND_RUN_REGISTRY": "file"})
    events: list[dict[str, Any]] = []
    events_file = run_env.get("WYND_EVENTS_FILE")

    def on_event(event: dict[str, Any]) -> None:
        events.append(event)
        if events_file:
            with open(events_file, "a", encoding="utf-8") as out:
                out.write(json.dumps(event, ensure_ascii=False) + "\n")

    name, t0 = f"example_{n}", time.perf_counter()
    try:
        result = run_plan(
            plan, inputs, env=run_env, stores=stores, run_id=f"run-example-{n}", on_event=on_event,
            cassette_mode=mode, cassette_root=cassette_root, record_root=record_root,
            cassette_literals=_literals({tmp: "<tmp>", process_dir: "<process>", ws.root: "<ws>"}),
        )
    except InvalidProcessInputs as err:
        return TestCase(name=name, outcome="error", message=f"invalid example inputs: {err}", duration_ms=_ms(t0))
    miss = _cassette_miss(events)
    if miss is not None:
        return TestCase(name=name, outcome="error", message=miss, duration_ms=_ms(t0))
    if result.exit != example.exit:
        detail = f" ({result.error.cause}: {result.error.message})" if result.error is not None else ""
        message = f"exit: expected {example.exit!r}, got {result.exit!r}{detail}"
        return TestCase(name=name, outcome="failed", message=message, duration_ms=_ms(t0))
    mismatches = match_outputs(sub_tmp(example.outputs, tmp), result.outputs)
    if mismatches:
        message = "\n".join(f"{m.field}: expected {m.expected!r}, got {m.actual!r}" for m in mismatches)
        return TestCase(name=name, outcome="failed", message=message, duration_ms=_ms(t0))
    return TestCase(name=name, outcome="passed", duration_ms=_ms(t0))


def _resolve_paths(inputs: Mapping[str, Any], input_types: Mapping[str, Any], process_dir: Path) -> dict[str, Any]:
    """Relative values of top-level `path` inputs are relative to the process dir."""
    out = dict(inputs)
    for name, value in inputs.items():
        node = input_types.get(name)
        if isinstance(node, TScalar) and node.name == "path" and isinstance(value, str) and value:
            if not Path(value).is_absolute():
                out[name] = str(process_dir / value)
    return out


def _cassette_miss(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        match event["type"]:
            case "step.end" if event.get("exit") == RESERVED_EXIT:
                outputs = event.get("outputs") or {}
                if outputs.get("cause") == "cassette_miss":
                    return outputs.get("message") or NO_RECORDING
            case "edge.check" if event.get("error_cause") == "cassette_miss":
                return event.get("reason") or NO_RECORDING
    return None


# --- shared ---------------------------------------------------------------------------------------------------------

def _promote_checked(staging: Path, dest: Path, backup: Path, replay: Callable[[], SuiteResult]) -> SuiteResult:
    """Promote the staged recordings into `dest` and replay; a failing replay restores the previous recordings."""
    shutil.rmtree(backup, ignore_errors=True)
    if dest.is_dir():
        shutil.copytree(dest, backup)
    promote(staging, dest)
    result = replay()
    if not result.passed:
        promote(backup, dest)
    return result


def _literals(paths: Mapping[Path, str]) -> dict[str, str]:
    """Normaliser literals for each path and, when different, its realpath (`/var` vs `/private/var`)."""
    out: dict[str, str] = {}
    for path, placeholder in paths.items():
        out[str(path)] = placeholder
        out[os.path.realpath(path)] = placeholder
    return out


def _closure_clean(ws: Workspace, pid: str) -> bool:
    from .git import dirty_paths, reference_closure

    return not dirty_paths(ws.root, reference_closure(ws, pid))


def _record(runs: RunRegistry, commit: str, pid: str, report: TestReport) -> None:
    """`step:<id>:<step_hash>` per step suite, `process:<cid>:<hash>` per child suite, `process:<pid>:<hash>` for the
    whole report (the root's own examples are part of it)."""
    at = report.finished_at
    for suite in report.suites:
        if suite.subject == f"process:{pid}":
            continue
        key = f"{suite.subject}:{suite.hash}" if suite.subject.startswith("process:") else \
            f"step:{suite.subject}:{suite.hash}"
        runs.put_test_result(TestResult(commit=commit, key=key, passed=suite.passed, counts=suite.counts, ran_at=at,
                                        report=suite.model_dump(mode="json")))
    totals = {key: sum(suite.counts.get(key, 0) for suite in report.suites) for key in COUNT_KEYS}
    runs.put_test_result(TestResult(commit=commit, key=f"process:{pid}:{report.process_hash}", passed=report.passed,
                                    counts=totals, ran_at=at, report=report.model_dump(mode="json")))


def _suite(
    subject: str, hash: str, cases: list[TestCase], *, problem: str | None = None, passed: bool | None = None
) -> SuiteResult:
    counts = {key: sum(case.outcome == key for case in cases) for key in COUNT_KEYS}
    if passed is None:
        passed = problem is None and counts["failed"] == 0 and counts["error"] == 0
    return SuiteResult(subject=subject, hash=hash, passed=passed and problem is None, counts=counts, cases=cases,
                       problem=problem)


def _status(result: TestResult | None) -> str:
    if result is None:
        return "missing"
    return "passed" if result.passed else "failed"


def _tail(text: str, limit: int = 2000) -> str:
    return text if len(text) <= limit else "…" + text[-limit:]


def _ms(t0: float) -> int:
    return int((time.perf_counter() - t0) * 1000)


def _now() -> datetime:
    return datetime.now(UTC)

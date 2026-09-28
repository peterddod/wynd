"""M5 optimise in the controller (PLAN §8.1 optimise row, §14 OPT-CTL; `$DRAFTS/08 §3.7–§3.9`): the report over the
TraceSink, submitting `--apply`, the `optimise` job through the real harness and in-process runner, and the two API
routes.

The fixture workspace `brief` (provider `fake`) has one agentic step `draft` (locked at `standard`, with a comment
header) and one agentic branch `draft.done[ok]` (locked at `standard`); its traces are seeded with clean live
executions, so every report recommends D1 (demote both to `cheap`). The job tests replace the three live test runs of
`wynd.controller.optimise` (`_live_step_suite`, `_live_process_suite`, `_record_results`) with scripted results;
`test_the_job_records_tests_and_commits_for_real` runs them for real (step venv, pytest, workers) with the `fake`
provider, offline, and the live-suite tests check record -> promote -> replay (and its rollback) on their own.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from support.ctl_api_client import api_client
from support.proc_env_workspaces import finish_locks, pyproject, src, to_yaml

import wynd.controller.optimise as optimise
from wynd.controller.errors import DirtyTree, Invalid, NotFound
from wynd.controller.jobs.inprocess import InProcessJobRunner
from wynd.controller.optimise import NothingToApply, optimise_report, run_optimise_job, submit_optimise
from wynd.process.jobs import JobContext, JobRecord
from wynd.process.optimise import OptimiseJobInput, TierChange
from wynd.process.testing import SuiteResult, TestCase
from wynd.process.workspace import load_workspace
from wynd.runtime.storage import stores_from_env
from wynd.spec.lockfiles import EdgeLockEntry, EdgesLock, check_hash, dump_lock, load_edges_lock, load_step_lock
from wynd.spec.workspace import step_module_name
from wynd.spec.yamlio import parse_model

PID = "brief"
PDIR = f"processes/{PID}"
DRAFT = f"{PDIR}/steps/draft"
STEP_LOCK = f"{DRAFT}/step.lock.yaml"
EDGES_LOCK = f"{PDIR}/edges.lock.yaml"
BRANCH = "draft.done[ok]"
CHECK = "The summary is short."
HEADER = "# Hand-written step; the review surface for this package.\n"
FAKE_SCRIPT = json.dumps({"responses": [
    {"match": {"input": {"transition": {"from": "draft.done"}}}, "output": {"take": True, "reason": "short enough"}},
    {"output": {"exit": "done", "summary": "short"}},
]})

PROCESS = {
    "kind": "process", "name": PID, "goal": "Summarise a text.", "provider": "fake", "entry": "draft",
    "inputs": {"text": "string"},
    "outputs": {"done": {"summary": "string"}, "review": {"summary": "string"}},
    "examples": [{"inputs": {"text": "a long text"}, "outputs": {"summary": "short"}}],
    "steps": {"draft": {"use": "./steps/draft"}},
    "edges": [{
        "from": "draft.done", "kind": "agentic",
        "to": [
            {"step": "$exit.done", "name": "ok", "check": CHECK, "with": {"summary": "steps.draft.outputs.summary"}},
            {"step": "$exit.review", "with": {"summary": "steps.draft.outputs.summary"}},
        ],
    }],
}

DRAFT_PY = src('''
    from typing import Literal

    from pydantic import BaseModel

    from wynd.runtime import AgenticStep


    class Draft(AgenticStep):
        """Summarise the text in one line."""

        class Input(BaseModel):
            text: str

        class Done(BaseModel):
            exit: Literal["done"] = "done"
            summary: str

        Output = Done

        def run(self, input: Input) -> Done: ...
''')

TEST_DRAFT_PY = src('''
    from pathlib import Path

    from wynd.runtime.testing import expect, load_step, run_step

    Draft = load_step(Path(__file__).parent)


    def test_example_1(tmp_path):
        result = run_step(Draft, {"text": "a long text"}, workspace=tmp_path)
        expect(result, exit="done", outputs={"summary": "short"})
''')

FILES = {
    ".gitignore": ".wynd/\n__pycache__/\n",
    "wynd.yaml": "process_roots: [processes]\n",
    f"{PDIR}/process.yaml": to_yaml(PROCESS),
    f"{DRAFT}/pyproject.toml": pyproject("brief-draft"),
    f"{DRAFT}/draft.py": DRAFT_PY,
    f"{DRAFT}/test_draft.py": TEST_DRAFT_PY,
    EDGES_LOCK: dump_lock(EdgesLock(edges={BRANCH: EdgeLockEntry(check_hash=check_hash(CHECK, None),
                                                                 tier="standard")})),
}
USAGE = {"input_tokens": 100, "output_tokens": 10, "cost_usd": 0.001, "latency_ms": 900.0, "calls": 1}


# --- fixtures --------------------------------------------------------------------------------------------------------

@pytest.fixture
def brief(git_repo, git, write_files, monkeypatch, shared_venvs) -> Path:
    """The `brief` workspace, committed on `main`; step venvs under the session's shared root, offline."""
    monkeypatch.setenv("UV_OFFLINE", "1")
    write_files(git_repo, FILES)
    finish_locks(git_repo, {DRAFT: {"tier": "standard", "thinking": "low",
                                    "retries": {"run": 2, "validation": 2, "tool": 1}}})
    lock = git_repo / STEP_LOCK
    lock.write_text(HEADER + lock.read_text())
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-q", "-m", "brief")
    (git_repo / ".wynd").mkdir()
    (git_repo / ".wynd" / "venvs").symlink_to(shared_venvs, target_is_directory=True)
    return git_repo


@pytest.fixture
def ctl(brief, make_controller):
    """A controller over `brief` whose jobs run in-process through the real harness."""
    stores = stores_from_env(os.environ, data_dir=brief / ".wynd")
    runner = InProcessJobRunner(env=dict(os.environ), workspace_root=brief, state_dir=brief / ".wynd", stores=stores)
    return make_controller(brief, runner=runner)


@pytest.fixture
def scripted(monkeypatch):
    """Replace the job's three test runs: `state["step"]`/`state["process"]` say whether they pass. The step run
    records the tier the lock holds while it runs and writes a cassette, like a real recording would."""
    state = {"step": True, "process": True, "replay": True, "calls": []}

    def step_suite(ctx, ws, pid, step_id, env, scratch):
        tier = load_step_lock(ws.root / STEP_LOCK).tier
        state["calls"].append(("step", step_id, tier, env["WYND_EVENTS_FILE"]))
        (ws.root / DRAFT / "cassettes").mkdir(exist_ok=True)
        (ws.root / DRAFT / "cassettes" / f"{tier}.json").write_text("{}\n")
        return suite(state["step"])

    def process_suite(ctx, ws, pid, env):
        state["calls"].append(("process", pid, load_edges_lock(ws.root / EDGES_LOCK).edges[BRANCH].tier))
        return suite(state["process"], failing="example_1")

    def record_results(ctx, pid):
        state["calls"].append(("replay", pid))
        head = git_head(ctx.workspace)
        return {"commit": head, "passed": state["replay"], "recorded": True}

    monkeypatch.setattr(optimise, "_live_step_suite", step_suite)
    monkeypatch.setattr(optimise, "_live_process_suite", process_suite)
    monkeypatch.setattr(optimise, "_record_results", record_results)
    return state


def suite(passed: bool, failing: str = "test_example_1") -> SuiteResult:
    cases = [TestCase(name=failing, outcome="passed" if passed else "failed", duration_ms=5),
             TestCase(name="test_other", outcome="passed", duration_ms=5)]
    counts = {"passed": 2 if passed else 1, "failed": 0 if passed else 1, "error": 0, "skipped": 0}
    return SuiteResult(subject="s", hash="", passed=passed, counts=counts, cases=cases)


def git_head(cwd: Path) -> str:
    return subprocess.run(["git", "-C", str(cwd), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


def seed_runs(ws: Path, n: int, *, tier: str = "standard", pid: str = PID, first: int = 1) -> list[str]:
    """`n` clean live runs of `pid` in the workspace's TraceSink: draft and the `ok` check at fake/`tier`."""
    stores = stores_from_env(os.environ, data_dir=ws / ".wynd")
    start = datetime(2026, 9, 1, tzinfo=UTC)
    ids = []
    for i in range(first, first + n):
        run_id = f"run_seed_{pid}_{i:03d}"
        at = (start + timedelta(minutes=i)).isoformat()
        stores.runs.create({"id": run_id, "kind": "run", "process": pid, "status": "succeeded", "created_at": at})
        for seq, event in enumerate(run_events(tier), start=1):
            stores.traces.write({"v": 1, "seq": seq, "ts": at, "run_id": run_id, **event})
        stores.traces.close(run_id)
        ids.append(run_id)
    return ids


def run_events(tier: str) -> list[dict]:
    model = {"provider": "fake", "model_id": "fake", "tier": tier, "thinking": "low"}
    return [
        {"type": "step.start", "step": "draft", "name": "draft", "process": PID, "span": 1, "parent": None,
         "id": f"{PID}#draft", "kind": "agentic", "run": 1},
        {"type": "model.call", "step": "draft", "span": 1, "parent": None, "provider": "fake", "tier": tier,
         "attempt": 1, "usage": USAGE, "cassette": "live"},
        {"type": "step.end", "step": "draft", "span": 1, "parent": None, "run": 1, "kind": "agentic", "exit": "done",
         "attempts": 1, "validation_failures": 0, "timings": {"duration_ms": 1000.0}, "usage": USAGE, "model": model,
         "replayed": False},
        {"type": "edge.check", "process": PID, "parent": None, "edge": "draft.done", "branch": 0, "branch_key": BRANCH,
         "target": "$exit.done", "take": True, "reason": "short", "attempts": 1, "validation_failures": 0,
         "provider": "fake", "tier": tier, "model_id": "fake", "usage": USAGE, "duration_ms": 500.0,
         "replayed": False},
        {"type": "run.end", "exit": "done", "status": "succeeded", "duration_ms": 1600.0},
    ]


def wait(ctl, job_id: str):
    return ctl.jobs.wait(job_id, timeout=300, poll=0.05)


def show(ws: Path, git, ref: str, rel: str) -> str:
    return git(ws, "show", f"{ref}:{rel}")


# --- the report ------------------------------------------------------------------------------------------------------

def test_the_report_reads_the_process_runs_at_the_closure_head(brief, ctl, git):
    seed_runs(brief, 3)
    seed_runs(brief, 4, pid="other")                                   # another process's runs never count
    report = optimise_report(ctl, PID, min_runs=3)
    assert report.process == PID and report.commit == git(brief, "rev-parse", "HEAD").strip()
    assert report.window == {"max_runs": 200, "runs_considered": 3, "live_calls": 3}
    assert report.rules["min_samples"] == 3
    assert [(u.unit, u.kind, u.tier) for u in report.units] == [("draft", "agentic", "standard"),
                                                               (f"edge:{BRANCH}", "edge", "standard")]
    assert [(c.unit, c.lock_path, c.lock_key, c.from_tier, c.to_tier, c.rule) for c in report.changes] == [
        ("draft", STEP_LOCK, None, "standard", "cheap", "D1"),
        (f"edge:{BRANCH}", EDGES_LOCK, BRANCH, "standard", "cheap", "D1")]


def test_the_default_threshold_keeps_tiers_until_enough_runs(brief, ctl):
    seed_runs(brief, 3)
    report = optimise_report(ctl, PID)
    assert report.changes == []
    assert {u.unit: u.recommendation.rule for u in report.units} == {"draft": "R0", f"edge:{BRANCH}": "R0"}


def test_max_runs_reads_only_the_newest_runs(brief, ctl):
    seed_runs(brief, 2, tier="cheap")                                  # older runs at another tier
    seed_runs(brief, 3, first=3)
    report = optimise_report(ctl, PID, max_runs=3, min_runs=3)
    assert report.window["runs_considered"] == 3
    assert set(report.units[0].by_tier) == {"fake/standard"}


def test_report_errors(brief, ctl):
    with pytest.raises(NotFound):
        optimise_report(ctl, "nope")
    with pytest.raises(Invalid):
        optimise_report(ctl, PID, min_runs=0)


# --- submitting ------------------------------------------------------------------------------------------------------

def test_submit_filters_units_and_refuses_an_empty_or_unknown_selection(brief, ctl, scripted):
    seed_runs(brief, 3)
    with pytest.raises(Invalid, match="unknown unit"):
        submit_optimise(ctl, PID, units=["nope"], min_runs=3)
    with pytest.raises(NothingToApply) as nothing:
        submit_optimise(ctl, PID, units=["draft"])                     # R0 at the default threshold
    assert nothing.value.details["changes"] == [] and nothing.value.http == 409

    job_id, report = submit_optimise(ctl, PID, units=["draft"], min_runs=3)
    assert [c.unit for c in report.changes] == ["draft"]
    job = wait(ctl, job_id)
    record = ctl.ctx.runner.status(job_id)
    assert record.job_kind == "optimise" and record.target_branch == "main"
    assert record.inputs["report_commit"] == report.commit
    assert [c["unit"] for c in record.inputs["changes"]] == ["draft"]
    assert [a["unit"] for a in job.report["applied"]] == ["draft"]


def test_submit_refuses_a_dirty_workspace(brief, ctl, write_files):
    seed_runs(brief, 3)
    write_files(brief, {"notes.txt": "uncommitted\n"})
    with pytest.raises(DirtyTree):
        submit_optimise(ctl, PID, min_runs=3)


# --- the job ---------------------------------------------------------------------------------------------------------

def test_apply_tests_each_change_commits_and_publishes_the_branch(brief, ctl, scripted, git):
    seed_runs(brief, 3)
    base = git(brief, "rev-parse", "HEAD").strip()
    job_id, report = submit_optimise(ctl, PID, min_runs=3)
    job = wait(ctl, job_id)
    assert job.status == "succeeded", job.error
    assert job.branch == f"wynd/optimise/{PID}/{job_id}" and job.result_commit
    assert git(brief, "rev-parse", job.branch).strip() == job.result_commit
    assert git(brief, "rev-parse", f"{job.result_commit}~1").strip() == base

    # the step's tests ran with the new tier in the lock; the process examples with both changes
    events = str(ctl.ctx.state_dir / "jobs" / job_id / "scratch" / "events.jsonl")
    assert scripted["calls"] == [("step", f"{PID}#draft", "cheap", events), ("process", PID, "cheap"),
                                 ("replay", PID)]

    # the committed locks: canonical text, the step lock's header kept, only the tier changed
    step_lock = show(brief, git, job.result_commit, STEP_LOCK)
    before = show(brief, git, base, STEP_LOCK)
    assert step_lock == before.replace("tier: standard\n", "tier: cheap\n") and step_lock.startswith(HEADER)
    edges = load_edges_lock_text(show(brief, git, job.result_commit, EDGES_LOCK))
    assert edges.edges[BRANCH].tier == "cheap"
    assert show(brief, git, job.result_commit, f"{DRAFT}/cassettes/cheap.json") == "{}\n"
    assert (brief / STEP_LOCK).read_text() == before                  # the user's checkout is untouched

    message = git(brief, "log", "-1", "--format=%B", job.result_commit)
    assert message == (
        f"wynd optimise: {PID}\n\n"
        "draft: standard -> cheap (D1: 0 failures, 0.0% validation failures, 1.00 attempts over 3 executions)\n"
        f"edge:{BRANCH}: standard -> cheap (D1: 0 failures, 0.0% validation failures, 1.00 attempts over 3 "
        "executions)\n\n"
        f"Wynd-Report-Commit: {report.commit[:12]}\n\n"
        f"Wynd-Job: {job_id}\n\n")
    # `ctx.commit` appends its trailer as a paragraph of its own: `Wynd-Job` is the commit's one git trailer
    trailers = git(brief, "log", "-1", "--format=%(trailers:only,unfold)", job.result_commit)
    assert trailers.strip() == f"Wynd-Job: {job_id}"

    assert job.report == {
        "branch": job.branch, "commit": job.result_commit, "report_commit": report.commit,
        "applied": [
            {"unit": "draft", "from_tier": "standard", "to_tier": "cheap", "rule": "D1",
             "tests": {"passed": 2, "failed": 0}},
            {"unit": f"edge:{BRANCH}", "from_tier": "standard", "to_tier": "cheap", "rule": "D1", "tests": None}],
        "rejected": [], "process_tests": {"passed": 2, "failed": 0},
        "usage": job.usage.model_dump(mode="json"),
    }
    assert job.artefacts["replay"] == {"commit": job.result_commit, "passed": True, "recorded": True}

    integrated = ctl.jobs.integrate(job_id)
    assert integrated.integration.mode == "fast_forward"
    assert load_step_lock(brief / STEP_LOCK).tier == "cheap"


def test_failing_step_tests_reject_the_change_and_restore_the_lock(brief, ctl, scripted, git):
    seed_runs(brief, 3)
    scripted["step"] = False
    before = (brief / STEP_LOCK).read_text()
    job = wait(ctl, submit_optimise(ctl, PID, min_runs=3)[0])
    assert job.status == "succeeded"
    assert job.report["rejected"] == [{
        "unit": "draft", "from_tier": "standard", "to_tier": "cheap",
        "reason": "tests failed live at cheap: 1/2 (test_example_1)"}]
    assert [a["unit"] for a in job.report["applied"]] == [f"edge:{BRANCH}"]
    assert show(brief, git, job.result_commit, STEP_LOCK) == before     # restored before the commit
    assert load_edges_lock_text(show(brief, git, job.result_commit, EDGES_LOCK)).edges[BRANCH].tier == "cheap"
    assert "rejected draft: standard -> cheap (tests failed live at cheap: 1/2 (test_example_1))" in \
        git(brief, "log", "-1", "--format=%B", job.result_commit)


def test_failing_process_examples_fail_the_job_without_a_branch(brief, ctl, scripted, git):
    seed_runs(brief, 3)
    scripted["process"] = False
    job = wait(ctl, submit_optimise(ctl, PID, min_runs=3)[0])
    assert job.status == "failed" and job.branch is None and job.result_commit is None
    assert job.error.message == ("process examples failed live after the tier changes (1/2 (example_1)); "
                                 "nothing committed")
    assert job.report["process_tests"] == {"passed": 1, "failed": 1} and job.report["commit"] is None
    assert [c[0] for c in scripted["calls"]] == ["step", "process"]
    assert git(brief, "branch", "--list", "wynd/optimise/*").strip() == ""


def test_a_failing_replay_of_the_new_commit_is_reported_not_fatal(brief, ctl, scripted):
    """Like `test_live`: the live gate decides the job; the replay results only feed the build gate."""
    seed_runs(brief, 3)
    scripted["replay"] = False
    job = wait(ctl, submit_optimise(ctl, PID, min_runs=3)[0])
    assert job.status == "succeeded" and job.result_commit is not None and job.error is None
    assert job.artefacts["replay"] == {"commit": job.result_commit, "passed": False, "recorded": True}
    assert "some suites fail; the build gate will refuse this commit" in ctl.jobs.logs(job.id).text


def job_context(ws: Path, tmp_path: Path, inputs: dict, commits: list) -> JobContext:
    """A context over the user's checkout itself (the tests that never reach a commit)."""
    now = datetime.now(UTC)
    job = JobRecord(id="job_1", job_kind="optimise", process=PID, ref="a" * 40, base_commit="a" * 40,
                    target_branch="main", inputs=inputs, status="running", runner="inprocess",
                    handler="wynd.controller.optimise:run_optimise_job", created_at=now, updated_at=now)
    state = ws / ".wynd"
    return JobContext(
        job=job, inputs=inputs, session=None, worktree=ws, workspace=ws, workspace_root=ws, state_dir=state,
        scratch=tmp_path / "scratch", runs=None, registry=None, log=lambda line: None,
        commit=lambda message, paths: commits.append(message) or "b" * 40, save_session=lambda s: None,
    )


def change(unit: str, **fields) -> dict:
    step = not unit.startswith("edge:")
    base = {"unit": unit, "lock_path": STEP_LOCK if step else EDGES_LOCK, "lock_key": None if step else BRANCH,
            "from_tier": "standard", "to_tier": "cheap", "rule": "D1",
            "evidence": {"n": 3, "fail_rate": 0.0, "vfail_rate": 0.0, "mean_attempts": 1.0}}
    return TierChange.model_validate({**base, **fields}).model_dump(mode="json")


def test_drifted_or_foreign_locks_are_rejected_and_nothing_is_committed(brief, tmp_path, scripted):
    before = {rel: (brief / rel).read_text() for rel in (STEP_LOCK, EDGES_LOCK)}
    changes = [
        change("draft", from_tier="strong"),                                       # the lock moved since the report
        change(f"edge:{BRANCH}", from_tier="cheap", to_tier="strong", rule="P1"),
        change("ghost", lock_path=f"{PDIR}/steps/ghost/step.lock.yaml"),           # not a step of the process
        change(f"edge:{BRANCH}", lock_path="processes/other/edges.lock.yaml"),      # not the process's own lock
        change("edge:draft.done[2]", lock_key="draft.done[2]"),                    # a branch the lock does not hold
    ]
    inputs = OptimiseJobInput.model_validate({"process": PID, "report_commit": "c" * 40, "changes": changes})
    commits: list[str] = []
    outcome = run_optimise_job(job_context(brief, tmp_path, {**inputs.model_dump(mode="json"),
                                                             "target_branch": "main"}, commits))
    assert (outcome.status, outcome.commit, commits, scripted["calls"]) == ("succeeded", None, [], [])
    assert outcome.report["applied"] == [] and outcome.report["branch"] is None
    assert [r["reason"] for r in outcome.report["rejected"]] == [
        "lock is now standard, report assumed strong",
        "lock is now standard, report assumed cheap",
        f"{PDIR}/steps/ghost/step.lock.yaml is not the lock of a process-local step ghost of {PID}",
        f"{BRANCH} is not a branch locked in {EDGES_LOCK}",
        f"draft.done[2] is not a branch locked in {EDGES_LOCK}",
    ]
    assert {rel: (brief / rel).read_text() for rel in before} == before


def test_usage_counts_the_live_calls_of_the_job(tmp_path):
    events = tmp_path / "events.jsonl"
    lines = [
        {"type": "model.call", "provider": "fake", "tier": "cheap", "cassette": "record", "usage": USAGE},
        {"type": "model.call", "provider": "fake", "tier": "cheap", "cassette": "record", "usage": USAGE},
        {"type": "model.call", "provider": "fake", "tier": "cheap", "cassette": "replay", "usage": USAGE},
        {"type": "model.call", "provider": "claude-code", "tier": "cheap", "cassette": "live",
         "usage": {**USAGE, "calls": 0}},
        {"type": "step.end", "usage": USAGE},
    ]
    events.write_text("".join(json.dumps(line) + "\n" for line in lines))
    usage = optimise._usage(events)
    assert (usage.calls, usage.input_tokens, usage.output_tokens) == (3, 300, 30)
    assert {key: u.calls for key, u in usage.by.items()} == {"fake/cheap": 2, "claude-code/cheap": 1}
    assert optimise._usage(tmp_path / "missing.jsonl").calls == 0


def load_edges_lock_text(text: str) -> EdgesLock:
    return parse_model(text, EdgesLock, EDGES_LOCK)


# --- the live suites ------------------------------------------------------------------------------------------------

def entry(key: str) -> str:
    return json.dumps({"wynd_cassette": 1, "key": key}) + "\n"


def fake_run(calls: list, record: bool, replay: bool):
    """A suite runner whose recording writes `new.json` into the staging dir."""
    def run(mode, target):
        calls.append((mode, target))
        if mode == "record":
            target.mkdir(parents=True)
            (target / "new.json").write_text(entry("new"))
        return suite(record if mode == "record" else replay)
    return run


@pytest.mark.parametrize(("before", "record", "replay", "modes", "after"), [
    (["old"], True, True, ["record", "replay"], ["new"]),
    (["old"], True, False, ["record", "replay"], ["old"]),     # the previous recordings come back
    (["old"], False, True, ["record"], ["old"]),               # a failed recording is never promoted
    ([], True, False, ["record", "replay"], []),
])
def test_live_suites_promote_only_recordings_that_pass_and_replay(tmp_path, before, record, replay, modes, after):
    dest = tmp_path / "pkg" / "cassettes"
    dest.mkdir(parents=True)
    (dest / "README").write_text("notes\n")                    # not a recording: never touched
    for key in before:
        (dest / f"{key}.json").write_text(entry(key))
    scratch = tmp_path / "scratch"
    (scratch / "recorded").mkdir(parents=True)
    (scratch / "recorded" / "stale.json").write_text(entry("stale"))   # a previous attempt's staging is cleared
    calls: list = []
    result = optimise._record_promote_replay(scratch, dest, fake_run(calls, record, replay))
    assert result.passed == (record and replay)
    assert calls == [(mode, scratch / "recorded" if mode == "record" else None) for mode in modes]
    assert sorted(p.name for p in dest.iterdir() if p.suffix == ".json") == [f"{key}.json" for key in after]
    assert [json.loads((dest / f"{key}.json").read_text())["key"] for key in after] == after
    assert (dest / "README").read_text() == "notes\n"


def test_the_live_step_suite_runs_in_the_step_venv_with_the_cassette_literals(brief, tmp_path, monkeypatch):
    import wynd.process.testing as testing

    calls = []

    def run_step_suite(pkg_dir, *, python, mode, env, junit, basetemp, record_dir=None):
        assert python.is_file() and junit.parent == basetemp.parent
        calls.append((pkg_dir, mode, record_dir, env))
        if record_dir is not None:
            record_dir.mkdir(parents=True)
            (record_dir / "a.json").write_text(entry("a"))
        return suite(True)

    monkeypatch.setattr(testing, "run_step_suite", run_step_suite)
    ctx = job_context(brief, tmp_path, {}, [])
    scratch = tmp_path / "steps" / "draft"
    result = optimise._live_step_suite(ctx, load_workspace(brief), PID, f"{PID}#draft",
                                       {"WYND_EVENTS_FILE": "events.jsonl"}, scratch)
    assert result.passed
    assert [call[:3] for call in calls] == [(brief / DRAFT, "record", scratch / "recorded"),
                                            (brief / DRAFT, "replay", None)]
    env = calls[0][3]
    assert (env["WYND_EVENTS_FILE"], env["WYND_DEFAULT_PROVIDER"]) == ("events.jsonl", "fake")
    literals = json.loads(env["WYND_CASSETTE_LITERALS"])
    assert literals[str(brief / PDIR)] == literals[os.path.realpath(brief / PDIR)] == "<process>"
    assert literals[str(brief)] == literals[os.path.realpath(brief)] == "<ws>"
    assert sorted(p.name for p in (brief / DRAFT / "cassettes").glob("*.json")) == ["a.json"]


# --- the real test runs ----------------------------------------------------------------------------------------------

def cassettes(ws: Path, rel: str) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((ws / rel).glob("*.json"))]


def test_the_job_records_tests_and_commits_for_real(brief, ctl, git, monkeypatch):
    monkeypatch.setenv("WYND_FAKE_PROVIDER_SCRIPT", FAKE_SCRIPT)
    seed_runs(brief, 3)
    job = wait(ctl, submit_optimise(ctl, PID, min_runs=3)[0])
    assert job.status == "succeeded", (job.error, job.report)
    assert job.report["applied"][0]["tests"] == {"passed": 1, "failed": 0}
    assert job.report["process_tests"] == {"passed": 1, "failed": 0}
    assert job.artefacts["replay"] == {"commit": job.result_commit, "passed": True, "recorded": True}
    # three live calls: the step suite's example, then the process example's step and branch check
    assert job.usage.calls == 3 and set(job.usage.by) == {"fake/cheap"}

    assert ctl.jobs.integrate(job.id).integration.mode == "fast_forward"
    step = cassettes(brief, f"{DRAFT}/cassettes")
    in_process = cassettes(brief, f"{PDIR}/cassettes/{step_module_name(f'{PID}#draft')}")
    checks = cassettes(brief, f"{PDIR}/cassettes/edges")
    assert (len(step), len(in_process), len(checks)) == (1, 1, 1)
    assert [c["tier"] for c in step + in_process + checks] == ["cheap"] * 3
    assert checks[0]["step"] == f"edge:{BRANCH}"

    # the promoted recordings replay: the process tests pass at the integrated commit without the script
    monkeypatch.delenv("WYND_FAKE_PROVIDER_SCRIPT")
    report = ctl.processes.test(PID)
    assert report.passed and report.recorded, report.model_dump_json(indent=1)


# --- API --------------------------------------------------------------------------------------------------------------

def test_the_api_serves_the_report_and_submits_jobs(brief, ctl, scripted):
    seed_runs(brief, 3)
    client = api_client(ctl)
    got = client.get(f"/api/processes/{PID}/optimise", params={"min_runs": 3})
    assert got.status_code == 200
    body = got.json()
    assert body["process"] == PID and [c["unit"] for c in body["changes"]] == ["draft", f"edge:{BRANCH}"]
    assert body["units"][0]["current"]["n"] == 3 and "call_latency_ms" not in body["units"][0]["current"]

    assert client.post(f"/api/processes/{PID}/optimise", json={"units": ["nope"], "min_runs": 3}).status_code == 422
    nothing = client.post(f"/api/processes/{PID}/optimise", json={})
    assert nothing.status_code == 409 and nothing.json()["error"]["code"] == "nothing_to_apply"
    assert client.get("/api/processes/nope/optimise").status_code == 404

    posted = client.post(f"/api/processes/{PID}/optimise", json={"units": [f"edge:{BRANCH}"], "min_runs": 3})
    assert posted.status_code == 201
    created = posted.json()
    assert [c["unit"] for c in created["changes"]] == [f"edge:{BRANCH}"]
    assert wait(ctl, created["job_id"]).status == "succeeded"

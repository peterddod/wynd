"""The test runner (PLAN §3.20, §6.6 PROC-ENV cases): hermetic step suites in real venvs, process examples through
real workers, cassette misses, `{tmp}`-stable keys, child suites, drift, records and the `test_live` job."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from support.proc_env_workspaces import (  # noqa: F401
    NOTES,
    NOTES_PROCESS,
    make_agentic,
    make_family,
    make_notes,
    src,
    to_yaml,
    validate_double,
)

import wynd.process.git as git_module
import wynd.process.testing as testing
from wynd.process.hashing import process_hash, step_hash
from wynd.process.jobs import JobContext, JobRecord
from wynd.process.testing import (
    SuiteResult,
    TestReport,
    run_process_examples,
    run_step_suite,
    run_test_live_job,
    run_tests,
)
from wynd.process.venvs import step_python
from wynd.process.workspace import load_workspace
from wynd.runtime.cassettes import NO_RECORDING, promote
from wynd.runtime.storage import stores_from_env
from wynd.spec.lockfiles import dump_lock, load_step_lock
from wynd.spec.workspace import step_module_name

FAKE_SCRIPT = json.dumps({"responses": [{"output": {"exit": "done", "line": "ok"}}]})


@pytest.fixture
def python(shared_venvs, monkeypatch):
    monkeypatch.setenv("UV_OFFLINE", "1")
    return step_python([], venv_root=shared_venvs)


@pytest.fixture
def runs(tmp_path):
    return stores_from_env({"WYND_HOME": str(tmp_path / "home")}, data_dir=tmp_path / "data").runs


@pytest.fixture
def git_double(monkeypatch):
    """PROC-GIT's closure and dirt checks, replaced: `state["dirty"]` is what `dirty_paths` reports."""
    state = {"dirty": [], "closure_calls": 0}

    def reference_closure(ws, pid):
        state["closure_calls"] += 1
        return ["wynd.yaml", f"processes/{pid}"]

    monkeypatch.setattr(git_module, "reference_closure", reference_closure)
    monkeypatch.setattr(git_module, "dirty_paths", lambda root, paths: list(state["dirty"]))
    monkeypatch.setattr(git_module, "closure_head", lambda root, paths, ref="HEAD": "c0ffee" * 6 + "abcd")
    return state


def by_subject(report: TestReport) -> dict[str, SuiteResult]:
    return {suite.subject: suite for suite in report.suites}


# --- run_step_suite -------------------------------------------------------------------------------------------------

def write_pkg(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def test_step_suite_parses_junit_with_the_full_failure_text(python, tmp_path):
    pkg = write_pkg(tmp_path / "proc" / "steps" / "mixed", {
        "test_mixed.py": src('''
            import os

            import pytest


            def test_ok():
                assert os.environ["WYND_CASSETTE_MODE"] == "record"
                assert os.environ["WYND_CASSETTE_RECORD_DIR"] == os.environ["EXPECTED_RECORD_DIR"]


            def test_bad():
                assert 1 + 1 == 3, "arithmetic is broken"


            @pytest.mark.skip(reason="not today")
            def test_later():
                pass
        '''),
    })
    # a conftest and an ini above the package must never apply (the suite is hermetic)
    write_pkg(tmp_path / "proc", {"conftest.py": "raise RuntimeError('outer conftest was imported')\n",
                                  "pytest.ini": "[pytest]\naddopts = --definitely-not-an-option\n"})
    out = tmp_path / "scratch"
    record = tmp_path / "staging"
    suite = run_step_suite(pkg, python=python, mode="record", env={**os.environ, "EXPECTED_RECORD_DIR": str(record)},
                           junit=out / "junit.xml", basetemp=out / "tmp", record_dir=record)
    assert (suite.passed, suite.problem) == (False, None)
    assert suite.counts == {"passed": 1, "failed": 1, "error": 0, "skipped": 1}
    cases = {case.name: case for case in suite.cases}
    assert cases["test_ok"].outcome == "passed"
    assert cases["test_bad"].outcome == "failed" and "arithmetic is broken" in cases["test_bad"].message
    assert "test_mixed.py:12: AssertionError" in cases["test_bad"].message       # the full text, not the summary
    assert cases["test_later"].outcome == "skipped"
    assert (out / "pytest.ini").read_text() == "[pytest]\naddopts = -p no:cacheprovider --import-mode=importlib\n"
    assert sorted(p.name for p in pkg.iterdir()) == ["test_mixed.py"]      # no cache, no bytecode left behind


def test_no_tests_collected_fails_the_suite(python, tmp_path):
    pkg = write_pkg(tmp_path / "empty", {"empty.py": "X = 1\n"})
    suite = run_step_suite(pkg, python=python, mode="replay", env=dict(os.environ), junit=tmp_path / "j/junit.xml",
                           basetemp=tmp_path / "j/tmp")
    assert (suite.passed, suite.problem, suite.cases) == (False, "no tests collected", [])


def test_a_passing_suite(python, tmp_path):
    pkg = write_pkg(tmp_path / "ok", {"test_ok.py": "def test_ok():\n    assert True\n"})
    suite = run_step_suite(pkg, python=python, mode="replay", env=dict(os.environ), junit=tmp_path / "j/junit.xml",
                           basetemp=tmp_path / "j/tmp")
    assert (suite.passed, suite.counts["passed"], suite.subject, suite.hash) == (True, 1, str(pkg), "")


# --- run_tests on the notes workspace --------------------------------------------------------------------------------

@pytest.fixture
def notes(make_repo, commit, validate_double):
    return make_notes(make_repo, commit)


def test_run_tests_replay_runs_step_suites_and_process_examples(notes, tmp_path, runs, git_double):
    ws = load_workspace(notes)
    events = tmp_path / "events.jsonl"
    report = run_tests(ws, "notes", mode="replay", commit=None, runs=runs, venv_root=notes / ".wynd" / "venvs",
                       scratch=tmp_path / "scratch", env={**os.environ, "WYND_EVENTS_FILE": str(events)})
    assert report.passed, report.model_dump_json(indent=1)
    assert [s.subject for s in report.suites] == ["notes#read", "notes#save", "process:notes"]
    suites = by_subject(report)
    assert suites["notes#read"].counts["passed"] == 2 and suites["notes#save"].counts["passed"] == 1
    assert [c.name for c in suites["process:notes"].cases] == ["example_1", "example_2"]
    lp = ws.load_process("notes")
    assert suites["notes#read"].hash == step_hash(ws.tree, lp.steps["read"].package)
    assert suites["process:notes"].hash == report.process_hash == process_hash(ws.tree, lp)
    assert (report.mode, report.commit, report.recorded) == ("replay", None, False)  # no commit: nothing recorded
    assert runs.get_test_result("x", f"process:notes:{report.process_hash}") is None
    # {tmp} became a fresh per-example dir that the save worker wrote into; the relative input found the data file
    note = tmp_path / "scratch" / "processes" / "notes" / "replay" / "example-1" / "tmp" / "out" / "note.txt"
    assert note.read_text() == "HELLO WORLD"
    logged = [json.loads(line) for line in events.read_text().splitlines()]
    assert {e["run_id"] for e in logged} == {"run-example-1", "run-example-2"}
    assert list((notes / NOTES).rglob("__pycache__")) == []                 # nothing written into the tree


def test_results_are_recorded_only_for_a_clean_closure(notes, tmp_path, runs, git_double):
    ws = load_workspace(notes)
    sha = "1" * 40
    git_double["dirty"] = ["processes/notes/process.yaml"]
    dirty = run_tests(ws, "notes", mode="replay", commit=sha, runs=runs, venv_root=notes / ".wynd/venvs",
                      scratch=tmp_path / "a")
    assert dirty.passed and not dirty.recorded
    assert runs.get_test_result(sha, f"process:notes:{dirty.process_hash}") is None

    git_double["dirty"] = []
    report = run_tests(ws, "notes", mode="replay", commit=sha, runs=runs, venv_root=notes / ".wynd/venvs",
                       scratch=tmp_path / "b")
    assert report.recorded
    whole = runs.get_test_result(sha, f"process:notes:{report.process_hash}")
    assert whole.passed and whole.counts == {"passed": 5, "failed": 0, "error": 0, "skipped": 0}
    assert TestReport.model_validate(whole.report).recorded
    suites = by_subject(report)
    for sid in ("notes#read", "notes#save"):
        assert runs.get_test_result(sha, f"step:{sid}:{suites[sid].hash}").passed
    # the root's own examples share the whole-report key (same process hash): it holds the full report
    assert suites["process:notes"].hash == report.process_hash
    assert whole.report == report.model_dump(mode="json")

    assert testing.tests_status(runs, ws, "notes", sha) == {
        "status": "passed", "steps": {"notes#read": "passed", "notes#save": "passed"}}
    assert testing.tests_status(runs, ws, "notes", "2" * 40) == {
        "status": "missing", "steps": {"notes#read": "missing", "notes#save": "missing"}}


def test_example_outputs_are_a_subset_match(notes, tmp_path, validate_double):
    process = {**NOTES_PROCESS, "examples": [
        {"inputs": {"src": "data/hello.txt"}, "env": {"OUT_DIR": "{tmp}/out"}, "outputs": {"words": 2}},
        {"inputs": {"src": "data/hello.txt"}, "env": {"OUT_DIR": "{tmp}/out"}, "outputs": {"words": 3}},
        {"inputs": {"src": "data/blank.txt"}},
    ]}
    (notes / NOTES / "process.yaml").write_text(to_yaml(process))
    suite = run_process_examples(load_workspace(notes), "notes", mode="replay", env=dict(os.environ),
                                 scratch=tmp_path / "s", venv_root=notes / ".wynd/venvs")
    outcomes = [(c.outcome, c.message) for c in suite.cases]
    assert outcomes == [
        ("passed", None),
        ("failed", "words: expected 3, got 2"),
        ("failed", "exit: expected 'done', got 'empty'"),
    ]
    assert not suite.passed and suite.counts == {"passed": 1, "failed": 2, "error": 0, "skipped": 0}


def test_interface_snapshot_drift_fails_the_step_suite(notes, tmp_path, git_double):
    path = notes / NOTES / "steps" / "read" / "step.lock.yaml"
    lock = load_step_lock(path)
    schema = lock.interface.input
    drifted = lock.interface.model_copy(update={"input": {**schema, "properties": {**schema["properties"],
                                                                                   "extra": {"type": "string"}}}})
    path.write_text(dump_lock(lock.model_copy(update={"interface": drifted})))
    report = run_tests(load_workspace(notes), "notes", mode="replay", commit=None, runs=None,
                       venv_root=notes / ".wynd/venvs", scratch=tmp_path / "s")
    read = by_subject(report)["notes#read"]
    assert not report.passed and not read.passed and read.cases == []
    assert read.problem == ("interface snapshot drift: step.lock.yaml interface differs from read:Read; "
                            "run wynd validate notes --sync-interfaces")
    assert by_subject(report)["notes#save"].passed


# --- child processes ------------------------------------------------------------------------------------------------

def test_a_childs_failing_example_fails_the_parent(make_repo, commit, validate_double, tmp_path, git_double):
    ws = make_family(make_repo, commit)
    report = run_tests(load_workspace(ws), "parent", mode="replay", commit=None, runs=None,
                       venv_root=ws / ".wynd/venvs", scratch=tmp_path / "s")
    suites = by_subject(report)
    assert list(suites) == ["child#double", "process:parent", "process:child"]
    assert suites["child#double"].passed and suites["process:parent"].passed
    assert not suites["process:child"].passed
    assert suites["process:child"].cases[0].message == "n: expected 5, got 4"
    assert not report.passed


def test_child_suites_are_recorded_under_their_own_process_key(make_repo, commit, validate_double, tmp_path, runs,
                                                               git_double):
    ws = make_family(make_repo, commit, child_expected=4)
    sha = "3" * 40
    report = run_tests(load_workspace(ws), "parent", mode="replay", commit=sha, runs=runs,
                       venv_root=ws / ".wynd/venvs", scratch=tmp_path / "s")
    assert report.passed and report.recorded
    child = by_subject(report)["process:child"]
    assert runs.get_test_result(sha, f"process:child:{child.hash}").passed
    assert runs.get_test_result(sha, f"process:parent:{report.process_hash}").passed


# --- agentic steps: cassette misses and {tmp}-stable keys ------------------------------------------------------------

@pytest.fixture
def agentic(make_repo, commit, validate_double):
    return make_agentic(make_repo, commit)


@pytest.mark.parametrize("pid", ["review", "strict"])
def test_a_cassette_miss_fails_the_example_whatever_the_exit(agentic, tmp_path, pid):
    """`review` recovers from the miss through `draft.error -> fallback` and ends `done` as its example expects;
    `strict`'s example expects `exit: error`, which the miss produces. Both must fail with the miss text."""
    suite = run_process_examples(load_workspace(agentic), pid, mode="replay", env=dict(os.environ),
                                 scratch=tmp_path / "s", venv_root=agentic / ".wynd/venvs")
    [case] = suite.cases
    assert case.outcome == "error" and not suite.passed
    assert case.message.startswith(NO_RECORDING + "\n  key: ")
    module = step_module_name("shared:draft")
    assert f"  cassettes: {agentic / 'processes' / pid / 'cassettes' / module}" in case.message
    assert "\n  request: " in case.message


def test_two_tmp_dirs_hit_the_same_cassette_keys(agentic, tmp_path):
    ws = load_workspace(agentic)
    env = {**os.environ, "WYND_FAKE_PROVIDER_SCRIPT": FAKE_SCRIPT}
    module = step_module_name("echo#note")
    keys = []
    for name in ("first", "second"):
        suite = run_process_examples(ws, "echo", mode="record", env=env, scratch=tmp_path / name / "s",
                                     venv_root=agentic / ".wynd/venvs", record_root=tmp_path / name / "rec")
        assert suite.passed, suite.model_dump_json(indent=1)
        keys.append(sorted(p.name for p in (tmp_path / name / "rec" / module).glob("*.json")))
    assert keys[0] == keys[1] and len(keys[0]) == 1
    assert not (agentic / "processes" / "echo" / "cassettes").exists()      # recording went to the staging dir

    promote(tmp_path / "first" / "rec", agentic / "processes" / "echo" / "cassettes")
    replayed = run_process_examples(ws, "echo", mode="replay", env=dict(os.environ), scratch=tmp_path / "third",
                                    venv_root=agentic / ".wynd/venvs")
    assert replayed.passed, replayed.model_dump_json(indent=1)


TEST_NOTE_PY = src('''
    from pathlib import Path

    from wynd.runtime.testing import expect, load_step, run_step

    Note = load_step(Path(__file__).parent)


    def test_example_1(tmp_path):
        result = run_step(Note, {"dest": str(tmp_path / "out"), "text": "hi"}, workspace=tmp_path)
        expect(result, exit="done", outputs={"line": "ok"})
''')


def test_live_mode_records_promotes_and_replays(agentic, tmp_path, git_double):
    (agentic / "processes/echo/steps/note/test_note.py").write_text(TEST_NOTE_PY)
    env = {**os.environ, "WYND_FAKE_PROVIDER_SCRIPT": FAKE_SCRIPT}
    report = run_tests(load_workspace(agentic), "echo", mode="live", commit=None, runs=None,
                       venv_root=agentic / ".wynd/venvs", scratch=tmp_path / "s", env=env)
    assert report.passed and report.mode == "live", report.model_dump_json(indent=1)
    step_cassettes = list((agentic / "processes/echo/steps/note/cassettes").glob("*.json"))
    process_cassettes = list((agentic / "processes/echo/cassettes" / step_module_name("echo#note")).glob("*.json"))
    assert len(step_cassettes) == 1 and len(process_cassettes) == 1

    # the promoted recordings now replay without the provider script
    replay = run_tests(load_workspace(agentic), "echo", mode="replay", commit=None, runs=None,
                       venv_root=agentic / ".wynd/venvs", scratch=tmp_path / "r")
    assert replay.passed, replay.model_dump_json(indent=1)


def test_a_failing_replay_check_restores_the_previous_recordings(tmp_path):
    dest, staging = tmp_path / "cassettes", tmp_path / "staging"
    for directory, name in ((dest, "old"), (staging, "new")):
        directory.mkdir()
        (directory / f"{name}.json").write_text(json.dumps({"wynd_cassette": 1, "key": name}))
    failed = SuiteResult(subject="s", hash="", passed=False, counts={}, cases=[])
    seen = []

    def replay():
        seen.append(sorted(p.name for p in dest.glob("*.json")))
        return failed

    assert testing._promote_checked(staging, dest, tmp_path / "backup", replay) is failed
    assert seen == [["new.json"]]                                             # replayed against the new recordings
    assert sorted(p.name for p in dest.glob("*.json")) == ["old.json"]        # then restored


# --- the test_live job ----------------------------------------------------------------------------------------------

def test_run_test_live_job_commits_then_records_at_the_new_closure_head(notes, tmp_path, monkeypatch, runs,
                                                                         git_double):
    calls, commits = [], []

    def fake_run_tests(ws, pid, *, mode, commit, runs, venv_root, scratch, env=None, log=None):
        calls.append({"ws": ws.root, "pid": pid, "mode": mode, "commit": commit, "runs": runs,
                      "venv_root": venv_root, "scratch": scratch})
        suites = [SuiteResult(subject="notes#read", hash="h", passed=mode == "replay", counts={}, cases=[])]
        now = datetime.now(UTC)
        return TestReport(process=pid, commit=commit, process_hash="p", mode=mode, passed=mode == "replay",
                          suites=suites, started_at=now, finished_at=now, recorded=commit is not None)

    monkeypatch.setattr(testing, "run_tests", fake_run_tests)
    now = datetime.now(UTC)
    job = JobRecord(id="job_1", job_kind="test_live", process="notes", ref="a" * 40, base_commit="a" * 40,
                    target_branch="main", inputs={"process": "notes"}, status="running", runner="inprocess",
                    handler="wynd.process.testing:run_test_live_job", created_at=now, updated_at=now)
    state = tmp_path / "user" / ".wynd"
    ctx = JobContext(
        job=job, inputs=job.inputs, session=None, worktree=notes, workspace=notes, workspace_root=tmp_path / "user",
        state_dir=state, scratch=state / "jobs/job_1/scratch", runs=runs, registry=None, log=lambda line: None,
        commit=lambda message, paths: commits.append((message, paths)) or "b" * 40, save_session=lambda s: None,
    )
    outcome = run_test_live_job(ctx)
    assert commits == [("wynd test --live: re-record cassettes for notes", None)]
    head = "c0ffee" * 6 + "abcd"
    assert [(c["mode"], c["commit"], c["runs"], c["venv_root"]) for c in calls] == [
        ("live", None, None, state / "venvs"), ("replay", head, runs, state / "venvs")]
    assert all(c["ws"] == notes for c in calls)
    assert calls[0]["scratch"] != calls[1]["scratch"]
    assert (outcome.status, outcome.commit) == ("failed", "b" * 40)
    assert outcome.error == "live tests failed: notes#read"
    assert outcome.report["mode"] == "live"
    assert outcome.artefacts == {"replay": {"commit": head, "passed": True, "recorded": True}}

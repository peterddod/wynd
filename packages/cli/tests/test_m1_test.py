"""`wynd test [--live]` (PLAN §9, §3.22, §3.20; `$DRAFTS/06 §9.2`). Replay runs the real step suites and process
examples of `p1` in its venv (offline); `--live` submits a `test_live` job to an in-process runner whose handlers
are fakes (`support.ctl_jobs_handlers`: `commit_file` commits `processes/<pid>/NOTES.md`, `failed_outcome` fails),
waits and integrates: exit 0 fast-forwarded, 1 failed job, 3 dirty workspace, 5 left for review."""

from __future__ import annotations

import json

import pytest

P1_YAML = "processes/p1/process.yaml"


@pytest.fixture
def ctl(workspace, make_controller, use_controller, job_handlers):
    return use_controller(make_controller(workspace, handlers=job_handlers))


def suite_rows(stdout: str) -> dict[str, list[str]]:
    lines = stdout.splitlines()
    assert lines[0].split() == ["SUITE", "PASSED", "TIME", "RESULT"]
    rows = {}
    for line in lines[1:]:
        if "  " not in line:
            break
        name, _, rest = line.partition("  ")
        rows[name.strip()] = rest.split()
    return rows


# --- replay -----------------------------------------------------------------------------------------------------------

def test_replay_passes_and_records_on_a_clean_closure(ctl, workspace, cli, git):
    result = cli("test", "p1")
    rows = suite_rows(result.stdout)
    assert {name: (row[0], row[-1]) for name, row in rows.items()} == {
        "p1#count": ("1/1", "ok"), "p1#upper": ("2/2", "ok"), "p1 (examples)": ("2/2", "ok")}
    head = git(workspace, "rev-parse", "HEAD").strip()
    assert result.stdout.splitlines()[-2:] == ["passed", f"recorded for {head[:7]} (process HEAD)"]
    assert "step suite p1#upper" in result.stderr and "process examples p1" in result.stderr
    assert ctl.processes.status("p1").tests == "passed"


def test_replay_on_a_dirty_closure_is_not_recorded(ctl, workspace, cli, write_files):
    write_files(workspace, {"processes/p1/notes.md": "dirty\n"})
    result = cli("test", "p1")
    assert result.stdout.splitlines()[-1] == "not recorded: uncommitted changes in processes/p1/notes.md"
    assert ctl.processes.status("p1").tests == "unknown"


def test_a_failing_example_exits_1(ctl, workspace, cli, write_files):
    text = (workspace / P1_YAML).read_text()
    write_files(workspace, {P1_YAML: text.replace("outputs: { words: 2 }", "outputs: { words: 3 }")})
    result = cli("test", "p1", code=1)
    rows = suite_rows(result.stdout)
    assert (rows["p1 (examples)"][0], rows["p1 (examples)"][-1]) == ("1/2", "FAILED")
    assert rows["p1#upper"][-1] == "ok"
    lines = result.stdout.splitlines()
    failed = next(i for i, line in enumerate(lines) if line.startswith("p1 (examples): FAILED "))
    assert lines[failed + 1].startswith("    ") and "words" in "\n".join(lines[failed + 1:])
    assert "FAILED" in lines and lines[-1].startswith("not recorded: uncommitted changes in ")


def test_replay_as_json(ctl, cli):
    doc = json.loads(cli("test", "p1", "--json").stdout)
    assert doc["passed"] is True and doc["recorded"] is True and doc["mode"] == "replay"
    assert [suite["subject"] for suite in doc["suites"]] == ["p1#count", "p1#upper", "process:p1"]


def test_a_design_process_cannot_be_tested(ctl, cli):
    result = cli("test", "p2", code=3)
    assert "design phase" in result.stderr and "hint: compile the process first (wynd compile)" in result.stderr


# --- live -------------------------------------------------------------------------------------------------------------

def test_live_submits_waits_and_fast_forwards(ctl, workspace, cli, git):
    head = git(workspace, "rev-parse", "HEAD").strip()
    result = cli("test", "p1", "--live")
    job_id = result.stderr.split()[1]
    assert result.stderr.startswith(f"submitted {job_id} (test_live) at {head[:7]}\n")
    assert "committed " in result.stderr                                        # the job log, streamed
    new_head = git(workspace, "rev-parse", "HEAD").strip()
    assert result.stdout.splitlines() == [f"fast-forwarded main to {new_head[:7]}"]
    assert git(workspace, "log", "-1", "--format=%s").strip() == "notes for p1"
    assert (workspace / "processes/p1/NOTES.md").is_file()
    job = ctl.jobs.get(job_id)
    assert (job.status, job.integration.mode) == ("succeeded", "fast_forward")


def test_live_as_json_is_the_integrated_job(ctl, cli):
    doc = json.loads(cli("test", "p1", "--live", "--json").stdout)
    assert (doc["kind"], doc["status"], doc["integration"]["mode"]) == ("test_live", "succeeded", "fast_forward")


def test_live_no_wait_prints_the_job_id(ctl, cli):
    result = cli("test", "p1", "--live", "--no-wait")
    job_id = result.stdout.strip()
    assert job_id.startswith("job_") and ctl.jobs.wait(job_id).status == "succeeded"
    assert ctl.jobs.get(job_id).integration is None


def test_live_failure_exits_1(workspace, make_controller, use_controller, cli, job_handlers):
    handlers = {**job_handlers, "test_live": "support.ctl_jobs_handlers:failed_outcome"}
    ctl = use_controller(make_controller(workspace, handlers=handlers))
    result = cli("test", "p1", "--live", code=1)
    job_id = result.stderr.split()[1]
    assert f"job {job_id} failed: tests failed: extract" in result.stderr
    assert ctl.jobs.get(job_id).integration is None


def test_live_left_for_review_exits_5(ctl, workspace, cli, commit):
    result = cli("test", "p1", "--live", "--no-integrate")
    job_id = result.stderr.split()[1]
    branch = f"wynd/test-live/p1/{job_id}"
    assert result.stdout.splitlines() == [f"succeeded; branch {branch} not integrated (wynd jobs integrate {job_id})"]

    commit(workspace, "concurrent edit", {"processes/p1/CHANGES.md": "edited on main\n"})
    review = cli("jobs", "integrate", job_id, code=5)
    assert review.stdout.splitlines() == [f"left for review: {branch} (conflicts: processes/p1/CHANGES.md)"]
    assert f"hint: git push origin {branch} && gh pr create --head {branch}" in review.stderr.splitlines()
    again = cli("jobs", "integrate", job_id, "--json", code=5)                 # idempotent
    assert json.loads(again.stdout)["integration"]["mode"] == "pr_branch"


def test_live_refuses_a_dirty_workspace(ctl, workspace, cli, write_files):
    write_files(workspace, {"processes/p1/notes.md": "dirty\n"})
    result = cli("test", "p1", "--live", code=3)
    assert result.stderr.splitlines() == [
        "error: the workspace has uncommitted changes (1 path(s)); jobs run from commits only",
        "  processes/p1/notes.md",
        "hint: commit or stash them, then try again",
    ]
    assert ctl.jobs.list() == []

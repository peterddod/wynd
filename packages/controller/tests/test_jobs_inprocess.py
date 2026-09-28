"""`InProcessJobRunner` + the harness end to end on real git worktrees and the file run registry (PLAN §3.18)."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from support.ctl_jobs_workspace import (
    checkout_path,
    file_at,
    log_text,
    make_runner,
    make_workspace,
    run_git,
    settle,
    wait_log,
    wait_status,
)
from wynd.controller.errors import Invalid, JobState
from wynd.controller.jobs import records
from wynd.controller.jobs.handlers import resolve_handler
from wynd.controller.jobs.inprocess import InProcessJobRunner
from wynd.controller.jobs.runners import WORKER_GONE, open_job_runner, pid_alive
from wynd.controller.jobs.subproc import SubprocessJobRunner
from wynd.process.jobs import new_job_record

H = "support.ctl_jobs_handlers"
HANDLERS = {
    "build": f"{H}:succeed",
    "test_live": f"{H}:commit_file",
    "compile": f"{H}:ask_then_finish",
    "bake": f"{H}:fail",
    "optimise": f"{H}:failed_outcome",
}


def runner_for(ws: Path, **overrides: str) -> InProcessJobRunner:
    return make_runner(ws, InProcessJobRunner, {**HANDLERS, **overrides})


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return make_workspace(tmp_path)


def test_succeeds_and_folds_the_outcome(ws: Path) -> None:
    runner = runner_for(ws)
    job_id = runner.submit("build", "HEAD", {"process": "alpha", "registry": None})
    record = settle(runner, job_id)

    assert record.status == "succeeded"
    assert record.job_kind == "build" and record.process == "alpha" and record.runner == "inprocess"
    assert record.handler == f"{H}:succeed"
    assert record.ref == run_git(ws, "rev-parse", "HEAD") == record.base_commit
    assert record.artefacts == {"answer": 42, "cwd_is_workspace": True}
    assert record.report["attempt"] == 1
    assert record.report["inputs"] == {"process": "alpha", "registry": None}
    assert record.report["workspace"] == str(checkout_path(ws, job_id))
    assert record.report["worktree"] == str(checkout_path(ws, job_id))
    assert record.report["workspace_root"] == str(ws)
    assert record.report["state_dir"] == str(ws / ".wynd")
    assert record.report["scratch"] == str(ws / ".wynd" / "jobs" / job_id / "scratch")
    assert record.usage.input_tokens == 10 and record.usage.by["fake/cheap"].cost_usd == 0.5
    assert record.started_at is not None and record.finished_at is not None and record.duration_ms is not None
    assert record.pid == os.getpid() and record.host == socket.gethostname()
    assert record.result_branch is None and record.error is None
    assert not checkout_path(ws, job_id).exists()               # removed on success
    assert runner.artefacts(job_id) == record.artefacts
    log = log_text(ws, job_id)
    assert re.search(rf"^\d\d:\d\d:\d\d hello from {job_id}$", log, re.M)
    assert re.search(r"^\d\d:\d\d:\d\d line one\n\d\d:\d\d:\d\d line two$", log, re.M)
    assert "job succeeded" in log


@pytest.mark.parametrize("subdir", ["", "examples/ws"])
def test_commit_is_published_as_the_result_branch(tmp_path: Path, subdir: str) -> None:
    ws = make_workspace(tmp_path, subdir=subdir)
    main_before = run_git(ws, "rev-parse", "main")
    runner = runner_for(ws)
    job_id = runner.submit("test_live", "HEAD", {"process": "alpha", "target_branch": "main", "delete": "write"})
    record = settle(runner, job_id)

    assert record.status == "succeeded", record.error
    assert record.workspace_rel == subdir
    assert record.result_branch == f"wynd/test-live/alpha/{job_id}"
    assert record.result_commit == record.artefacts["committed"]
    assert run_git(ws, "rev-parse", f"refs/heads/{record.result_branch}") == record.result_commit
    assert run_git(ws, "rev-parse", "main") == main_before            # the user's branch is untouched
    message = run_git(ws, "log", "-1", "--format=%B", record.result_commit)
    assert message.startswith("notes for alpha") and f"Wynd-Job: {job_id}" in message
    prefix = f"{subdir}/" if subdir else ""
    changed = run_git(ws, "diff-tree", "--no-commit-id", "--name-status", "-r", record.result_commit).splitlines()
    assert sorted(changed) == [f"A\t{prefix}processes/alpha/NOTES.md", f"D\t{prefix}processes/alpha/proto/write.yaml"]
    assert file_at(ws, record.result_commit, "processes/alpha/NOTES.md") == f"written by {job_id}"
    assert not checkout_path(ws, job_id).exists()
    assert not (ws / "processes/alpha/NOTES.md").exists()                # nothing leaks into the user's checkout


def test_handler_exception_fails_and_keeps_the_checkout(ws: Path) -> None:
    runner = runner_for(ws)
    job_id = runner.submit("bake", "HEAD", {"process": "alpha"})
    record = settle(runner, job_id)

    assert record.status == "failed"
    assert record.error["message"] == "RuntimeError: boom"
    assert "Traceback" in record.error["detail"]
    log = log_text(ws, job_id)
    assert "about to fail" in log and "Traceback (most recent call last)" in log and "RuntimeError: boom" in log
    assert (checkout_path(ws, job_id) / "half-done.txt").read_text() == "left for inspection\n"
    assert run_git(ws, "worktree", "list").count(str(checkout_path(ws, job_id))) == 1


def test_failed_outcome_keeps_the_checkout(ws: Path) -> None:
    runner = runner_for(ws)
    job_id = runner.submit("optimise", "HEAD", {"process": "alpha", "target_branch": "main"})
    record = settle(runner, job_id)
    assert record.status == "failed"
    assert record.error == {"message": "tests failed: extract", "detail": None}
    assert checkout_path(ws, job_id).is_dir()


def test_unknown_handler_function_fails_the_job(ws: Path) -> None:
    runner = runner_for(ws, build=f"{H}:no_such_handler")
    record = settle(runner, runner.submit("build", "HEAD", {"process": "alpha"}))
    assert record.status == "failed"
    assert "has no function 'no_such_handler'" in record.error["message"]
    with pytest.raises(ValueError, match="module:function"):
        resolve_handler("no_colon_here")


def test_awaiting_input_without_session_is_a_failure(ws: Path) -> None:
    runner = runner_for(ws, build=f"{H}:awaiting_without_session")
    record = settle(runner, runner.submit("build", "HEAD", {"process": "alpha"}))
    assert record.status == "failed"
    assert "without a session" in record.error["message"]


def test_logs_come_in_whole_lines_with_offsets(ws: Path, tmp_path: Path) -> None:
    gate = tmp_path / "gate"
    runner = runner_for(ws, build=f"{H}:wait_gate")
    job_id = runner.submit("build", "HEAD", {"process": "alpha", "gate": str(gate)})
    wait_status(runner, job_id, "running")
    wait_log(ws, job_id, "waiting for")

    text, offset, done = runner.logs(job_id)
    assert "waiting for" in text and text.endswith("\n") and not done
    assert offset == len(text.encode())
    gate.touch()
    settle(runner, job_id)
    rest, end, done = runner.logs(job_id, offset)
    assert done and "job succeeded" in rest
    assert end == len(log_text(ws, job_id).encode())
    assert runner.logs(job_id, end) == ("", end, True)


def test_answering_requeues_the_same_job(ws: Path) -> None:
    runner = runner_for(ws)
    inputs = {"process": "alpha", "target_branch": "main", "answers": {}}
    job_id = runner.submit("compile", "HEAD", inputs)
    first = settle(runner, job_id)

    assert first.status == "awaiting_input"
    assert first.session["questions"][0]["id"] == "read.example1"
    assert first.questions == first.session["questions"]
    assert first.finished_at is not None
    assert first.result_branch == f"wynd/compile/alpha/{job_id}"
    wip = first.result_commit
    assert not checkout_path(ws, job_id, 1).exists()               # removed while awaiting input
    assert runner.logs(job_id)[2] is True                           # awaiting input counts as done for logs

    runner.requeue(job_id, wip, {**inputs, "answers": {"read.example1": "accept"}})
    second = settle(runner, job_id)
    assert second.id == job_id and second.attempt == 2
    assert second.status == "succeeded", second.error
    assert second.ref == wip and second.base_commit == first.base_commit
    assert second.report["session_seen"] == first.session
    assert second.report["answers"] == {"read.example1": "accept"}
    assert second.report["head_at_start"] == wip
    assert second.result_branch == first.result_branch               # same branch name across attempts
    assert run_git(ws, "rev-parse", f"refs/heads/{second.result_branch}") == second.result_commit
    assert run_git(ws, "rev-parse", f"{second.result_commit}~1") == wip
    assert second.questions == [] and second.session["state"] == "done"
    assert second.usage.input_tokens == 101 and second.usage.calls == 2   # totals over both attempts
    assert second.duration_ms >= first.duration_ms
    assert f"---- attempt 2 (resumed at {wip[:7]}) ----" in log_text(ws, job_id)


def test_requeue_needs_a_job_awaiting_input(ws: Path) -> None:
    runner = runner_for(ws)
    job_id = runner.submit("build", "HEAD", {"process": "alpha"})
    settle(runner, job_id)
    with pytest.raises(JobState):
        runner.requeue(job_id, "HEAD", {"process": "alpha"})


def _queued_record(ws: Path, runner: InProcessJobRunner, kind: str = "build") -> str:
    """A queued record that no runner started."""
    record = new_job_record(kind, "HEAD", {"process": "alpha", "target_branch": "main"}, ws_root=ws,
                            runner=runner.name, handler=HANDLERS[kind])
    records.create(runner.stores.runs, record)
    return record.id


def test_cancel_queued_and_awaiting_jobs(ws: Path) -> None:
    runner = runner_for(ws)
    queued = _queued_record(ws, runner)
    runner.cancel(queued)
    assert runner.status(queued).status == "cancelled"
    assert runner.status(queued).finished_at is not None
    runner.cancel(queued)                                             # cancelling twice is harmless

    awaiting = runner.submit("compile", "HEAD", {"process": "alpha", "target_branch": "main"})
    assert settle(runner, awaiting).status == "awaiting_input"
    runner.cancel(awaiting)
    assert runner.status(awaiting).status == "cancelled"

    done = runner.submit("build", "HEAD", {"process": "alpha"})
    settle(runner, done)
    with pytest.raises(JobState):
        runner.cancel(done)


def test_a_cancelled_queued_job_never_runs(ws: Path) -> None:
    from wynd.controller.jobs.checkout import WorktreeCheckout
    from wynd.controller.jobs.harness import execute_job

    runner = runner_for(ws)
    job_id = _queued_record(ws, runner)
    runner.cancel(job_id)
    record = execute_job(job_id, checkout=WorktreeCheckout(ws, ws / ".wynd"), stores=runner.stores,
                         registry=runner.stores.registry)
    assert record.status == "cancelled" and record.started_at is None


def test_running_in_process_job_cannot_be_cancelled(ws: Path, tmp_path: Path) -> None:
    gate = tmp_path / "gate"
    runner = runner_for(ws, build=f"{H}:wait_gate")
    job_id = runner.submit("build", "HEAD", {"process": "alpha", "gate": str(gate)})
    wait_status(runner, job_id, "running")
    with pytest.raises(JobState, match="cannot be cancelled while running"):
        runner.cancel(job_id)
    gate.touch()
    assert settle(runner, job_id).status == "succeeded"


def test_a_dead_host_process_fails_the_job(ws: Path) -> None:
    runner = runner_for(ws)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    job_id = _queued_record(ws, runner)
    records.update(runner.stores.runs, job_id, status="running", pid=dead.pid, host=socket.gethostname(),
                   started_at=datetime.now(UTC))
    record = runner.status(job_id)
    assert record.status == "failed"
    assert record.error == {"message": WORKER_GONE, "detail": None}
    assert record.finished_at is not None

    elsewhere = _queued_record(ws, runner)
    records.update(runner.stores.runs, elsewhere, status="running", pid=dead.pid, host="another-host")
    assert runner.status(elsewhere).status == "running"               # a pid on another machine is not ours to judge


def test_submit_needs_a_handler_for_the_kind(ws: Path) -> None:
    runner = make_runner(ws, InProcessJobRunner, {"build": f"{H}:succeed"})
    with pytest.raises(Invalid):
        runner.submit("bake", "HEAD", {"process": "alpha"})


def test_open_job_runner_uses_entry_points(ws: Path) -> None:
    kw = {"env": dict(os.environ), "workspace_root": ws, "state_dir": ws / ".wynd",
          "stores": make_runner(ws, InProcessJobRunner, HANDLERS).stores}
    assert isinstance(open_job_runner("inprocess", **kw), InProcessJobRunner)
    subprocess_runner = open_job_runner("subprocess", **kw, handlers={"build": f"{H}:succeed"})
    assert isinstance(subprocess_runner, SubprocessJobRunner)
    assert subprocess_runner.handlers == {"build": f"{H}:succeed"}
    assert open_job_runner("inprocess", **kw).handlers["compile"] == "wynd.compiler.jobs:run_compile_job"
    with pytest.raises(Invalid, match="unknown job runner 'nope'"):
        open_job_runner("nope", **kw)


def test_pid_alive() -> None:
    assert pid_alive(os.getpid())
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    assert not pid_alive(dead.pid)

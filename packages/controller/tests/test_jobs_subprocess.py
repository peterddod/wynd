"""`SubprocessJobRunner`: the same lifecycle with one `python -m wynd.controller.jobs.worker` per job, SIGTERM
cancel, reaped workers, a crashing worker, and the worker's own entry point (PLAN §3.18, §8.1)."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

import pytest

from support.ctl_jobs_workspace import (
    CONTROLLER_TESTS,
    checkout_path,
    log_text,
    make_runner,
    make_workspace,
    run_git,
    settle,
    wait_log,
    wait_status,
)
from wynd.controller.jobs import records, worker
from wynd.controller.jobs.runners import WORKER_GONE
from wynd.controller.jobs.subproc import SubprocessJobRunner
from wynd.process.jobs import new_job_record

H = "support.ctl_jobs_handlers"
HANDLERS = {
    "build": f"{H}:succeed",
    "test_live": f"{H}:commit_file",
    "compile": f"{H}:ask_then_finish",
    "bake": f"{H}:fail",
    "optimise": f"{H}:wait_gate",
}


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return make_workspace(tmp_path)


def runner_for(ws: Path, **overrides: str) -> SubprocessJobRunner:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(CONTROLLER_TESTS),
                                                                      os.environ.get("PYTHONPATH")]))}
    return make_runner(ws, SubprocessJobRunner, {**HANDLERS, **overrides}, env=env)


def reaped(runner: SubprocessJobRunner, job_id: str, timeout_s: float = 30) -> bool:
    """Poll `status` until the runner has reaped the job's worker."""
    deadline = time.monotonic() + timeout_s
    while job_id in runner._procs:
        runner.status(job_id)
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def test_lifecycle_in_a_worker_process(ws: Path) -> None:
    runner = runner_for(ws)
    job_id = runner.submit("test_live", "HEAD", {"process": "alpha", "target_branch": "main"})
    record = settle(runner, job_id)

    assert record.status == "succeeded", record.error
    assert record.runner == "subprocess"
    assert record.pid is not None and record.pid != os.getpid()
    assert record.result_branch == f"wynd/test-live/alpha/{job_id}"
    assert run_git(ws, "rev-parse", f"refs/heads/{record.result_branch}") == record.result_commit
    assert not checkout_path(ws, job_id).exists()
    assert f"committed {record.result_commit}" in log_text(ws, job_id)
    assert reaped(runner, job_id)
    assert runner.status(job_id).cpu_ms is not None                    # the worker adds its CPU time at the end
    text, offset, done = runner.logs(job_id)
    assert done and "job succeeded" in text and offset == len(text.encode())


def test_failure_in_a_worker_keeps_the_checkout(ws: Path) -> None:
    runner = runner_for(ws)
    job_id = runner.submit("bake", "HEAD", {"process": "alpha"})
    record = settle(runner, job_id)
    assert record.status == "failed" and record.error["message"] == "RuntimeError: boom"
    assert "Traceback" in log_text(ws, job_id)
    assert checkout_path(ws, job_id).is_dir()


def test_awaiting_input_and_requeue_start_a_new_worker(ws: Path) -> None:
    runner = runner_for(ws)
    inputs = {"process": "alpha", "target_branch": "main"}
    job_id = runner.submit("compile", "HEAD", inputs)
    first = settle(runner, job_id)
    assert first.status == "awaiting_input"
    assert reaped(runner, job_id)

    runner.requeue(job_id, first.result_commit, {**inputs, "answers": {"read.example1": "accept"}})
    second = settle(runner, job_id)
    assert second.status == "succeeded" and second.attempt == 2
    assert second.pid != first.pid
    assert second.report["answers"] == {"read.example1": "accept"}
    assert second.cpu_ms >= first.cpu_ms


def test_sigterm_cancels_a_running_job(ws: Path, tmp_path: Path) -> None:
    runner = runner_for(ws)
    job_id = runner.submit("optimise", "HEAD", {"process": "alpha", "target_branch": "main",
                                                "gate": str(tmp_path / "never")})
    wait_status(runner, job_id, "running")
    wait_log(ws, job_id, "waiting for")
    runner.cancel(job_id)
    record = settle(runner, job_id)
    assert record.status == "cancelled"
    assert record.finished_at is not None
    assert "job cancelled" in log_text(ws, job_id)
    assert not checkout_path(ws, job_id).exists()                      # only failed jobs keep their checkout
    assert reaped(runner, job_id)


def test_a_crashed_worker_fails_the_job(ws: Path) -> None:
    runner = runner_for(ws, build=f"{H}:crash")
    job_id = runner.submit("build", "HEAD", {"process": "alpha"})
    record = settle(runner, job_id)
    assert record.status == "failed"
    assert record.error == {"message": WORKER_GONE, "detail": None}
    assert "crashing" in log_text(ws, job_id)
    assert job_id not in runner._procs                                  # reaped: no zombie left behind


def test_a_worker_that_cannot_start_fails_the_job(ws: Path) -> None:
    """The worker exits 70 before touching the record (here: an unknown storage backend); the runner notices."""
    runner = runner_for(ws)
    runner.env = {**runner.env, "WYND_RUN_REGISTRY": "nope"}
    job_id = runner.submit("build", "HEAD", {"process": "alpha"})
    record = settle(runner, job_id)
    assert record.status == "failed" and record.error["message"] == WORKER_GONE
    assert "StorageConfigError" in log_text(ws, job_id)


def test_worker_main_runs_a_queued_job_in_process(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker entry point itself: loads `.env` when present, runs the job, records CPU time, exits 0."""
    (ws / ".env").write_text("WYND_TEST_FROM_DOTENV=1\n")
    loaded = []
    monkeypatch.setattr("wynd.controller.envfile.load_into_environ", lambda root: loaded.append(root))
    stores_runner = make_runner(ws, SubprocessJobRunner, HANDLERS)
    record = new_job_record("build", "HEAD", {"process": "alpha"}, ws_root=ws, runner="subprocess",
                            handler=f"{H}:succeed")
    records.create(stores_runner.stores.runs, record)
    previous = signal.getsignal(signal.SIGTERM)
    try:
        assert worker.main(["--workspace", str(ws), "--job", record.id]) == 0
    finally:
        signal.signal(signal.SIGTERM, previous)
    done = records.load(stores_runner.stores.runs, record.id)
    assert done.status == "succeeded" and done.cpu_ms is not None
    assert loaded == [ws]


def test_worker_exits_70_for_an_unknown_job(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    previous = signal.getsignal(signal.SIGTERM)
    try:
        assert worker.main(["--workspace", str(ws), "--job", "job_missing"]) == worker.EXIT_INFRA
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert "no job 'job_missing'" in capsys.readouterr().err


def test_clone_checkout_needs_a_remote(ws: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner = make_runner(ws, SubprocessJobRunner, HANDLERS)
    record = new_job_record("build", "HEAD", {"process": "alpha"}, ws_root=ws, runner="subprocess",
                            handler=f"{H}:succeed")
    records.create(runner.stores.runs, record)
    previous = signal.getsignal(signal.SIGTERM)
    try:
        assert worker.main(["--workspace", str(ws), "--job", record.id, "--checkout", "clone"]) == worker.EXIT_INFRA
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert "--checkout clone needs --remote" in capsys.readouterr().err
    assert records.load(runner.stores.runs, record.id).status == "queued"

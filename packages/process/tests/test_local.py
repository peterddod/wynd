"""`run_local`: a tiny two-step deterministic workspace end to end through real venv workers (PLAN §6.1 local row)."""

from __future__ import annotations

import os

import pytest
from support.proc_env_workspaces import NOTES, make_notes, validate_double  # noqa: F401

import wynd.process.local as local_module
from wynd.process.local import run_local
from wynd.process.workspace import load_workspace
from wynd.runtime.errors import InvalidProcessInputs
from wynd.runtime.storage import stores_from_env


@pytest.fixture
def notes(make_repo, commit, validate_double):
    return make_notes(make_repo, commit)


@pytest.fixture
def stores(tmp_path):
    return stores_from_env({"WYND_HOME": str(tmp_path / "home")}, data_dir=tmp_path / "data")


@pytest.fixture
def closed(monkeypatch):
    """Counts `WorkerPool.close` calls made by `run_local`."""
    calls = []

    class Pool(local_module.WorkerPool):
        def close(self):
            calls.append(self)
            super().close()

    monkeypatch.setattr(local_module, "WorkerPool", Pool)
    return calls


def test_run_local_end_to_end_with_real_workers(notes, stores, tmp_path, closed):
    out = tmp_path / "out"
    events: list[dict] = []
    env = {**os.environ, "OUT_DIR": str(out)}
    result = run_local(load_workspace(notes), "notes", {"src": str(notes / NOTES / "data" / "hello.txt")}, env=env,
                       stores=stores, run_id="run-e2e", on_event=events.append, metadata={"trigger": "manual"})

    assert (result.run_id, result.exit, result.status) == ("run-e2e", "done", "succeeded")
    assert result.outputs == {"name": "note.txt", "words": 2}
    assert (out / "note.txt").read_text() == "HELLO WORLD"                   # written by the save worker
    assert [(e["step"], e["exit"]) for e in events if e["type"] == "step.end"] == [("read", "done"), ("save", "done")]
    starts = {e["step"]: e for e in events if e["type"] == "step.start"}
    assert starts["read"]["venv"] == starts["save"]["venv"]                  # one venv: identical (empty) deps
    assert [e["type"] for e in events][0] == "run.start" and events[0]["mode"] == "local"
    assert events[0]["metadata"] == {"trigger": "manual"}
    assert result.workspace is None                                          # deleted: no error handler ran
    record = stores.runs.get("run-e2e")
    assert (record["status"], record["exit"], record["meta"]) == ("succeeded", "done", {"trigger": "manual"})
    assert [e["type"] for e in stores.traces.read("run-e2e")] == [e["type"] for e in events]
    assert len(closed) == 1


def test_a_declared_non_error_exit(notes, stores):
    result = run_local(load_workspace(notes), "notes", {"src": str(notes / NOTES / "data" / "blank.txt")},
                       env=dict(os.environ), stores=stores)
    assert (result.exit, result.outputs, result.status) == ("empty", {}, "succeeded")


def test_relative_input_paths_are_not_resolved(notes, stores):
    """The caller decides what a relative path is relative to; the read worker sees it relative to the run
    workspace, fails, and the default handler ends the run with `$exit.error` (workspace kept)."""
    result = run_local(load_workspace(notes), "notes", {"src": "data/hello.txt"}, env=dict(os.environ),
                       stores=stores)
    assert (result.exit, result.status) == ("error", "failed")
    assert (result.error.cause, result.error.step) == ("step_error", "read")
    assert result.error.step_error.cause == "exception"
    assert result.workspace is not None


def test_invalid_inputs_are_refused_and_the_pool_is_closed(notes, stores, closed):
    with pytest.raises(InvalidProcessInputs):
        run_local(load_workspace(notes), "notes", {"nope": 1}, env=dict(os.environ), stores=stores)
    assert len(closed) == 1
    assert stores.runs.list() == []                                          # refused before any run record

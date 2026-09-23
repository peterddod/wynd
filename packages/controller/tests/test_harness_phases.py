"""`execute_job` called directly: the prepare/finalize phases of kube build Jobs (PLAN §3.18 `PHASE_HANDLERS`), the
`handlers` override, records it must leave alone, a failing checkout, and `CloneCheckout` against a bare remote
(including the worker's `--checkout clone` entry)."""

from __future__ import annotations

import signal
from pathlib import Path

import pytest

from support.ctl_jobs_workspace import (
    checkout_path,
    log_text,
    make_workspace,
    open_stores,
    run_git,
)
from wynd.controller.jobs import handlers as handler_table
from wynd.controller.jobs import records, worker
from wynd.controller.jobs.checkout import CloneCheckout, WorktreeCheckout
from wynd.controller.jobs.harness import execute_job
from wynd.process.jobs import new_job_record

H = "support.ctl_jobs_handlers"


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return make_workspace(tmp_path)


@pytest.fixture
def phases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(handler_table.PHASE_HANDLERS, "build", {"prepare": f"{H}:prepare",
                                                                "finalize": f"{H}:finalize"})


def queued(stores, ws: Path, kind: str = "build", handler: str = f"{H}:succeed", **inputs) -> str:
    inputs = {"process": "alpha", **inputs}
    if kind in ("compile", "test_live", "optimise"):
        inputs.setdefault("target_branch", "main")
    record = new_job_record(kind, "HEAD", inputs, ws_root=ws, runner="kube", handler=handler)
    records.create(stores.runs, record)
    return record.id


def run(job_id: str, stores, checkout, **kw):
    return execute_job(job_id, checkout=checkout, stores=stores, registry=stores.registry, **kw)


def test_prepare_then_finalize(ws: Path, tmp_path: Path, phases: None, monkeypatch: pytest.MonkeyPatch) -> None:
    context_dir = tmp_path / "build-context"
    monkeypatch.setenv("WYND_BUILD_CONTEXT_DIR", str(context_dir))
    stores = open_stores(ws)
    checkout = WorktreeCheckout(ws, ws / ".wynd")
    job_id = queued(stores, ws)
    head = run_git(ws, "rev-parse", "HEAD")

    prepared = run(job_id, stores, checkout, phase="prepare")
    assert prepared.status == "running"                                 # stays running between the phases
    assert prepared.finished_at is None and prepared.started_at is not None
    assert prepared.artefacts == {"prepared": {"commit": head, "context_dir": str(context_dir)}}
    assert (context_dir / "Dockerfile").read_text() == "FROM scratch\n"
    assert not checkout_path(ws, job_id).exists()

    assert run(job_id, stores, checkout, phase="prepare").status == "running"   # not queued: left alone

    final = run(job_id, stores, checkout, phase="finalize")
    assert final.status == "succeeded" and final.finished_at is not None
    assert final.artefacts["prepared"] == prepared.artefacts["prepared"]
    assert final.artefacts["image"] == f"wynd/alpha:{head[:12]}"
    assert final.artefacts["dockerfile"] == "FROM scratch\n"
    assert final.duration_ms >= prepared.duration_ms
    log = log_text(ws, job_id)
    assert log.index("preparing") < log.index("finalizing")

    dto = records.to_dto(final)
    assert dto.build is not None and dto.build.image == final.artefacts["image"]
    assert dto.build.build_dir == str(context_dir) and dto.build.tests.source == "registry"


def test_finalize_needs_a_prepared_running_job(ws: Path, phases: None) -> None:
    stores = open_stores(ws)
    job_id = queued(stores, ws)
    record = run(job_id, stores, WorktreeCheckout(ws, ws / ".wynd"), phase="finalize")
    assert record.status == "queued" and record.started_at is None


def test_a_kind_without_phases_fails_its_prepare(ws: Path, phases: None) -> None:
    stores = open_stores(ws)
    job_id = queued(stores, ws, kind="bake")
    record = run(job_id, stores, WorktreeCheckout(ws, ws / ".wynd"), phase="prepare")
    assert record.status == "failed" and record.error["message"] == "ValueError: bake jobs have no prepare phase"


def test_handlers_override_the_recorded_handler(ws: Path) -> None:
    stores = open_stores(ws)
    job_id = queued(stores, ws, handler=f"{H}:fail")
    record = run(job_id, stores, WorktreeCheckout(ws, ws / ".wynd"), handlers={"build": f"{H}:succeed"})
    assert record.status == "succeeded" and record.artefacts["answer"] == 42


def test_a_checkpointed_session_is_kept(ws: Path) -> None:
    stores = open_stores(ws)
    job_id = queued(stores, ws, handler=f"{H}:checkpoint_only")
    record = run(job_id, stores, WorktreeCheckout(ws, ws / ".wynd"))
    assert record.status == "succeeded" and record.session == {"state": "running", "checkpoint": 1}


def test_finished_records_are_returned_unchanged(ws: Path) -> None:
    stores = open_stores(ws)
    job_id = queued(stores, ws)
    first = run(job_id, stores, WorktreeCheckout(ws, ws / ".wynd"))
    again = run(job_id, stores, WorktreeCheckout(ws, ws / ".wynd"))
    assert first.status == "succeeded" and again == first


def test_a_checkout_failure_fails_the_job(ws: Path) -> None:
    stores = open_stores(ws)
    job_id = queued(stores, ws)
    records.update(stores.runs, job_id, ref="0" * 40)
    record = run(job_id, stores, WorktreeCheckout(ws, ws / ".wynd"))
    assert record.status == "failed" and record.error["message"].startswith("GitError")
    assert "Traceback" in log_text(ws, job_id)


def bare_remote(ws: Path, tmp_path: Path) -> str:
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "clone", "--quiet", "--bare", run_git(ws, "rev-parse", "--show-toplevel"), str(remote))
    return str(remote)


@pytest.mark.parametrize("subdir", ["", "examples/ws"])
def test_clone_checkout_pushes_the_result_branch(tmp_path: Path, subdir: str) -> None:
    ws = make_workspace(tmp_path, subdir=subdir)
    remote = bare_remote(ws, tmp_path)
    stores = open_stores(ws)
    job_id = queued(stores, ws, kind="test_live", handler=f"{H}:commit_file")
    workdir = tmp_path / "pod" / "repo"
    checkout = CloneCheckout(remote, workdir, subdir)

    record = run(job_id, stores, checkout)
    assert record.status == "succeeded", record.error
    branch = f"wynd/test-live/alpha/{job_id}"
    assert record.result_branch == branch
    assert run_git(Path(remote), "rev-parse", f"refs/heads/{branch}") == record.result_commit
    assert run_git(ws, "branch", "--list", branch) == ""               # published to the remote, not locally
    assert run_git(workdir, "rev-parse", "HEAD") == record.result_commit
    assert (workdir / subdir / "processes/alpha/NOTES.md").exists()   # the pod's clone stays in place
    assert log_text(workdir / subdir, job_id) != ""                    # the state dir is the clone's workspace


def test_clone_checkout_phases_reuse_the_clone(ws: Path, tmp_path: Path, phases: None) -> None:
    remote = bare_remote(ws, tmp_path)
    stores = open_stores(ws)
    job_id = queued(stores, ws)
    workdir = tmp_path / "pod" / "repo"
    run_git(tmp_path, "clone", "--quiet", remote, str(workdir))       # what the checkout init container does
    marker = workdir / ".git" / "made-by-init-container"
    marker.touch()

    assert run(job_id, stores, CloneCheckout(remote, workdir, ""), phase="prepare").status == "running"
    final = run(job_id, stores, CloneCheckout(remote, workdir, ""), phase="finalize")
    assert final.status == "succeeded" and marker.exists()
    assert final.artefacts["commit"] == run_git(ws, "rev-parse", "HEAD")


def test_worker_with_a_clone_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--checkout clone --remote URL`: the workspace is `<clone>/<subdir>` and the clone is made there."""
    ws = make_workspace(tmp_path, subdir="examples/ws")
    remote = bare_remote(ws, tmp_path)
    monkeypatch.setenv("WYND_DATA_DIR", str(tmp_path / "shared-data"))   # the pod's shared run registry
    stores = open_stores(ws)
    job_id = queued(stores, ws, kind="test_live", handler=f"{H}:commit_file")
    pod_ws = tmp_path / "pod" / "repo" / "examples" / "ws"
    previous = signal.getsignal(signal.SIGTERM)
    try:
        code = worker.main(["--workspace", str(pod_ws), "--job", job_id, "--checkout", "clone", "--remote", remote])
    finally:
        signal.signal(signal.SIGTERM, previous)
    assert code == 0
    record = records.load(stores.runs, job_id)
    assert record.status == "succeeded", record.error
    assert (tmp_path / "pod" / "repo" / ".git").is_dir()
    assert run_git(Path(remote), "rev-parse", f"refs/heads/{record.result_branch}") == record.result_commit

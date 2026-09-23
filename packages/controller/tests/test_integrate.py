"""Integration through the controller (PLAN §3.18 "Integration"): the git lock + `wynd.process.git.integrate`, the
record update, test-result re-keying, `DirtyTree` when the user's checkout refuses the fast-forward, idempotence, and
`WYND_GIT_REMOTE` fetch/push. Jobs are real test_live jobs run by the in-process runner."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from support.ctl_jobs_workspace import (
    make_context,
    make_runner,
    make_workspace,
    run_git,
    settle,
    write_files,
)
from support.ctl_jobs_workspace import commit as commit_files
from wynd.controller.errors import DirtyTree, JobState
from wynd.controller.jobs import records
from wynd.controller.jobs.inprocess import InProcessJobRunner
from wynd.controller.jobs.integrate import integrate
from wynd.controller.jobs.service import JobService
from wynd.process.hashing import process_hash, step_hash
from wynd.process.jobs import JobRecord
from wynd.process.workspace import CommitTree, load_workspace
from wynd.runtime.storage.models import TestResult

H = "support.ctl_jobs_handlers"
HANDLERS = {"test_live": f"{H}:commit_file", "compile": f"{H}:succeed", "build": f"{H}:succeed",
            "optimise": f"{H}:failed_outcome"}


def service_for(ws: Path, env: dict[str, str] | None = None) -> JobService:
    runner = make_runner(ws, InProcessJobRunner, HANDLERS, env=env)
    return JobService(make_context(ws, runner, env=env), ctl=None)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return make_workspace(tmp_path)


@pytest.fixture
def service(ws: Path) -> JobService:
    return service_for(ws)


def run_job(service: JobService, pid: str = "alpha", kind: str = "test_live", **inputs) -> JobRecord:
    job = service.submit(kind, pid, inputs)
    return settle(service.ctx.runner, job.id)


def branch_exists(ws: Path, branch: str) -> bool:
    return run_git(ws, "branch", "--list", branch) != ""


def keys_at(ws: Path, sha: str, pid: str) -> list[str]:
    """The test-result keys of `pid`'s closure at `sha` (as `wynd.process.testing` records them)."""
    at = load_workspace(ws, CommitTree(ws, sha))
    lp = at.load_process(pid)
    keys = [f"step:{sid}:{step_hash(at.tree, pkg)}" for sid, pkg in lp.closure_packages().items()]
    return keys + [f"process:{cid}:{process_hash(at.tree, child)}" for cid, child in lp.closure_processes().items()]


def record_results(service: JobService, ws: Path, job: JobRecord) -> list[str]:
    keys = keys_at(ws, job.result_commit, job.process)
    for key in keys:
        service.ctx.stores.runs.put_test_result(TestResult(
            commit=job.result_commit, key=key, passed=True, counts={"passed": 2}, ran_at=datetime.now(UTC),
            job_id=job.id))
    return keys


@pytest.mark.parametrize("subdir", ["", "examples/ws"])
def test_fast_forward_when_the_target_did_not_move(tmp_path: Path, subdir: str) -> None:
    ws = make_workspace(tmp_path, subdir=subdir)
    service = service_for(ws)
    job = run_job(service)
    dto = service.integrate(job.id)

    assert dto.integration is not None
    assert dto.integration.mode == "fast_forward" and dto.integration.head == job.result_commit
    assert dto.integration.branch == job.result_branch and dto.integration.target == "main"
    assert dto.integration.skipped_commits == 0 and dto.integration.pr_url is None
    assert run_git(ws, "rev-parse", "main") == job.result_commit
    assert (ws / "processes/alpha/NOTES.md").read_text() == f"written by {job.id}\n"   # the checkout moved too
    assert not branch_exists(ws, job.result_branch)
    stored = records.load(service.ctx.stores.runs, job.id).integration
    assert stored == dto.integration.model_dump(mode="json")


def test_integration_is_idempotent(ws: Path, service: JobService) -> None:
    job = run_job(service)
    first = service.integrate(job.id).integration
    second = service.integrate(job.id).integration
    assert second == first                                             # the stored result, not a second attempt


def test_rebase_copies_test_results_to_the_new_commit(ws: Path, service: JobService) -> None:
    job = run_job(service)
    keys = record_results(service, ws, job)
    unrelated = commit_files(ws, "docs", {"README.md": "an intervening commit outside the closure\n"})

    result = integrate(service.ctx, records.load(service.ctx.stores.runs, job.id))
    assert result.mode == "rebased" and result.skipped_commits == 1
    assert result.head != job.result_commit
    assert run_git(ws, "rev-parse", "main") == result.head
    assert run_git(ws, "rev-parse", f"{result.head}~1") == unrelated
    assert not branch_exists(ws, job.result_branch)
    assert keys_at(ws, result.head, "alpha") == keys                   # same closure content, same keys
    runs = service.ctx.stores.runs
    for key in keys:
        copied = runs.get_test_result(result.head, key)
        assert copied is not None and copied.commit == result.head and copied.job_id == job.id
        assert runs.get_test_result(job.result_commit, key) is not None


@pytest.mark.parametrize(("pid", "touched"), [
    ("alpha", "processes/alpha/proto/read.yaml"),       # the process dir
    ("beta", "shared/steps/util/proto.yaml"),           # a referenced root step
    ("beta", "processes/alpha/proto/write.yaml"),       # a child process
    ("beta", "wynd.yaml"),                              # the workspace config
])
def test_intervening_closure_commits_leave_a_pr_branch(ws: Path, service: JobService, pid: str,
                                                       touched: str) -> None:
    job = run_job(service, pid)
    text = (ws / touched).read_text()
    tip = commit_files(ws, "touch the closure", {touched: text + "# edited on main\n"})

    dto = service.integrate(job.id)
    assert dto.integration.mode == "pr_branch"
    assert dto.integration.conflicts == [touched]
    assert f"gh pr create --head {job.result_branch}" in dto.integration.reason
    assert branch_exists(ws, job.result_branch)
    assert run_git(ws, "rev-parse", f"refs/heads/{job.result_branch}") == job.result_commit
    assert run_git(ws, "rev-parse", "main") == tip


def test_a_reference_added_only_on_the_branch_counts(ws: Path, service: JobService) -> None:
    """Closure union: the branch makes alpha use `shared:util`; a main commit touching it must block the rebase."""
    alpha = (ws / "processes/alpha/process.yaml").read_text()
    uses_util = alpha.replace("write: {use: './steps/write'}", "write: {use: 'shared:util'}")
    assert uses_util != alpha
    job = run_job(service, path="processes/alpha/process.yaml", text=uses_util)
    commit_files(ws, "util", {"shared/steps/util/proto.yaml": (ws / "shared/steps/util/proto.yaml").read_text()
                              + "# edited\n"})
    assert service.integrate(job.id).integration.mode == "pr_branch"


def test_local_changes_that_block_the_fast_forward_raise_dirty_tree(ws: Path, service: JobService) -> None:
    job = run_job(service, delete="write")
    keys = record_results(service, ws, job)
    main_before = commit_files(ws, "docs", {"README.md": "moved on\n"})
    write_files(ws, {"processes/alpha/proto/write.yaml": "a local, uncommitted edit\n"})

    with pytest.raises(DirtyTree) as caught:
        service.integrate(job.id)
    assert caught.value.details == {"paths": ["processes/alpha/proto/write.yaml"]}
    assert records.load(service.ctx.stores.runs, job.id).integration is None   # nothing recorded
    assert run_git(ws, "rev-parse", "main") == main_before
    assert branch_exists(ws, job.result_branch)

    run_git(ws, "checkout", "--quiet", "--", "processes/alpha/proto/write.yaml")
    result = service.integrate(job.id).integration
    assert result.mode == "fast_forward"                        # the branch was already rebased by the first try
    assert result.head != job.result_commit and run_git(ws, "rev-parse", "main") == result.head
    assert not (ws / "processes/alpha/proto/write.yaml").exists()
    assert all(service.ctx.stores.runs.get_test_result(result.head, key) is not None for key in keys)


def test_a_target_that_is_not_checked_out_moves_by_update_ref(ws: Path, service: JobService) -> None:
    job = run_job(service)
    run_git(ws, "checkout", "--quiet", "-b", "elsewhere")
    result = service.integrate(job.id).integration
    assert result.mode == "fast_forward"
    assert run_git(ws, "rev-parse", "main") == job.result_commit
    assert run_git(ws, "symbolic-ref", "--short", "HEAD") == "elsewhere"
    assert not (ws / "processes/alpha/NOTES.md").exists()      # the checked-out branch is left alone


def test_a_job_without_a_commit_integrates_as_noop(ws: Path) -> None:
    service = service_for(ws)
    service.ctx.runner.handlers["test_live"] = f"{H}:succeed"
    job = run_job(service)
    assert job.result_branch is None
    result = service.integrate(job.id).integration
    assert result.mode == "noop" and result.target == "main" and result.reason == "the job produced no commit"
    assert records.load(service.ctx.stores.runs, job.id).integration["mode"] == "noop"


def test_only_succeeded_commit_jobs_integrate(ws: Path, service: JobService) -> None:
    failed = run_job(service, kind="optimise")
    assert failed.status == "failed"
    with pytest.raises(JobState):
        service.integrate(failed.id)
    build = run_job(service, kind="build")
    with pytest.raises(JobState):
        service.integrate(build.id)


def test_git_remote_fetch_and_push(ws: Path, tmp_path: Path) -> None:
    """Kube-style: the result branch exists only on the remote; integration fetches it, fast-forwards the target
    and pushes the target back."""
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--quiet", "--bare", str(remote))
    service = service_for(ws, env={**os.environ, "WYND_GIT_REMOTE": str(remote)})
    job = run_job(service)
    run_git(ws, "push", "--quiet", str(remote), f"refs/heads/{job.result_branch}:refs/heads/{job.result_branch}")
    run_git(ws, "branch", "--quiet", "-D", job.result_branch)

    result = service.integrate(job.id).integration
    assert result.mode == "fast_forward" and result.head == job.result_commit
    assert run_git(ws, "rev-parse", "main") == job.result_commit
    assert run_git(remote, "rev-parse", "refs/heads/main") == job.result_commit

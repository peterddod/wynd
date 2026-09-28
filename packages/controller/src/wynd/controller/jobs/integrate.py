"""Controller wrapper around `wynd.process.git.integrate` (PLAN §3.18 "Integration"; `$DRAFTS/06 §5.9`).

Holds the git lock, fetches result branches from `WYND_GIT_REMOTE` when set (and pushes the advanced target back),
copies test results to the new commit when the integrated head differs from the job's commit (after a rebase), and
stores the `IntegrationResult` on the job record. Idempotent: a job integrated once returns its stored result.

When the target branch is checked out and the fast-forward is refused only because it would overwrite local changes,
nothing is recorded and `DirtyTree` is raised (HTTP 409 `dirty_tree`: the web commits and retries).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from wynd.controller.errors import DirtyTree, JobState
from wynd.controller.jobs import records
from wynd.process import git
from wynd.process.errors import WyndProcessError
from wynd.process.git import IntegrationResult
from wynd.process.hashing import process_hash, step_hash
from wynd.process.jobs import COMMIT_KINDS
from wynd.process.workspace import CommitTree, load_workspace

if TYPE_CHECKING:
    from pathlib import Path

    from wynd.controller.controller import ControllerContext
    from wynd.process.jobs import JobRecord
    from wynd.runtime.storage.base import RunRegistry


def integrate(ctx: ControllerContext, job: JobRecord) -> IntegrationResult:
    if job.integration is not None:
        return IntegrationResult.model_validate(job.integration)
    if job.job_kind not in COMMIT_KINDS or job.status != "succeeded" or job.target_branch is None:
        raise JobState(f"job {job.id} is a {job.status} {job.job_kind} job; only succeeded compile, test_live and "
                       "optimise jobs are integrated")
    branch, target = job.result_branch, job.target_branch
    if branch is None or job.result_commit is None:
        result = IntegrationResult(mode="noop", branch="", target=target, at=datetime.now(UTC),
                                   reason="the job produced no commit")
        records.update(ctx.stores.runs, job.id, integration=result.model_dump(mode="json"))
        return result

    remote = ctx.env.get("WYND_GIT_REMOTE")
    with ctx.git_lock:
        if remote:
            git.git(ctx.root, "fetch", "--quiet", remote, f"+refs/heads/{branch}:refs/heads/{branch}")
        result = git.integrate(ctx.root, process_id=job.process, base_sha=job.base_commit, branch=branch,
                               target_branch=target, scratch_dir=ctx.state_dir / "integrate" / job.id)
        if result.mode == "pr_branch" and _ff_blocked_by_worktree(ctx.root, branch, target):
            raise DirtyTree(
                f"integrating {branch} would overwrite uncommitted changes in the checkout of {target}",
                details={"paths": result.conflicts},
                hint="commit or stash those changes, then integrate the job again",
            )
        if remote and result.mode in ("fast_forward", "rebased"):
            git.git(ctx.root, "push", "--quiet", remote, f"refs/heads/{target}:refs/heads/{target}")

    records.update(ctx.stores.runs, job.id, integration=result.model_dump(mode="json"))
    if result.head is not None and result.mode in ("fast_forward", "rebased") and result.head != job.result_commit:
        try:
            copy_test_results(ctx.stores.runs, ctx.root, job.process, job.result_commit, result.head)
        except WyndProcessError:
            pass                                      # the integration stands; the results can be re-run
    return result


def _ff_blocked_by_worktree(root: Path, branch: str, target: str) -> bool:
    """The target is checked out and the (possibly rebased) branch already contains it: the fast-forward was
    possible, so only the checkout refused it."""
    repo = git.toplevel(root)
    if git.worktree_for_branch(repo, target) is None:
        return False
    return git.is_ancestor(repo, f"refs/heads/{target}", f"refs/heads/{branch}")


def copy_test_results(runs: RunRegistry, root: Path, pid: str, old: str, new: str) -> int:
    """Copy the test results recorded at `old`'s closure HEAD to `new`'s (same closure content, so the same keys);
    -> how many were copied."""
    ws = load_workspace(root, CommitTree(root, new))
    closure = git.reference_closure(ws, pid)
    old_head = git.closure_head(root, git.reference_closure(load_workspace(root, CommitTree(root, old)), pid), old)
    new_head = git.closure_head(root, closure, new)
    if old_head is None or new_head is None or old_head == new_head:
        return 0
    lp = ws.load_process(pid)
    keys = [f"step:{sid}:{step_hash(ws.tree, pkg)}" for sid, pkg in sorted(lp.closure_packages().items())]
    keys += [f"process:{cid}:{process_hash(ws.tree, child)}" for cid, child in lp.closure_processes().items()]
    copied = 0
    for key in keys:
        found = runs.get_test_result(old_head, key)
        if found is not None:
            runs.put_test_result(found.model_copy(update={"commit": new_head}))
            copied += 1
    return copied

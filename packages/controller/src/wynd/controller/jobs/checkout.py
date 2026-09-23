"""Job checkout backends (PLAN §3.18; `$DRAFTS/06 §6.3`).

Local: `git worktree add --detach .wynd/jobs/<id>/checkout-<attempt> <ref>` under the git lock (worktrees share refs,
so a result branch is published with `update-ref`). Kube: clone, and publish with
`git push -f origin <sha>:refs/heads/<branch>`.

Besides the protocol's three operations, a backend names the workspace the harness hands to handlers:
`workspace_root` (the user's workspace, holding the shared `.wynd/venvs` and `.wynd/build`) and `state_dir`
(`workspace_root / ".wynd"`: job logs and scratch dirs).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from wynd.controller.git import GitLock
from wynd.process import git
from wynd.process.errors import GitError
from wynd.spec.workspace import STATE_DIR

if TYPE_CHECKING:
    from wynd.process.jobs import JobRecord


@dataclass
class Checkout:
    worktree: Path                  # git toplevel of the job checkout (detached at the job ref)
    workspace: Path                 # worktree / job.workspace_rel
    commit: str
    remote: str | None = None       # set once a branch was pushed to a remote (integration fetches it first)


class CheckoutBackend(Protocol):
    workspace_root: Path
    state_dir: Path

    def prepare(self, job: JobRecord) -> Checkout: ...
    def push_branch(self, co: Checkout, branch: str, commit: str) -> None: ...
    def cleanup(self, co: Checkout, *, keep: bool) -> None: ...


class WorktreeCheckout:
    """Local runners: a detached worktree per attempt under `.wynd/jobs/<id>/`."""

    def __init__(self, workspace_root: Path, state_dir: Path) -> None:
        self.workspace_root = Path(workspace_root).absolute()
        self.state_dir = Path(state_dir).absolute()
        self.lock = GitLock(self.state_dir / "locks" / "git.lock")

    def prepare(self, job: JobRecord) -> Checkout:
        path = self.state_dir / "jobs" / job.id / f"checkout-{job.attempt}"
        repo = git.toplevel(self.workspace_root)
        with self.lock:
            if path.exists():                     # left behind by an attempt that died mid-way
                git.remove_worktree(repo, path)
            git.add_worktree(repo, path, job.ref)
        return Checkout(worktree=path, workspace=path / job.workspace_rel, commit=git.rev_parse(path, "HEAD"))

    def push_branch(self, co: Checkout, branch: str, commit: str) -> None:
        git.set_branch(co.worktree, branch, commit)

    def cleanup(self, co: Checkout, *, keep: bool) -> None:
        if keep:
            return
        with self.lock:
            git.remove_worktree(git.toplevel(self.workspace_root), co.worktree)


class CloneCheckout:
    """Kube pods: clone the remote, push result branches back to it.

    `workdir` is the clone (a pod's checkout init container may have made it already); `subdir` is the workspace
    inside it. The clone lives in the pod's own volume, so `cleanup` leaves it: the worker's state dir is inside it.
    The clone is made with `init` + `fetch` rather than `git clone` because the job log (under the state dir) may
    already exist in `workdir` when `prepare` runs.
    """

    def __init__(self, remote_url: str, workdir: Path, subdir: str) -> None:
        self.remote_url = remote_url
        self.workdir = Path(workdir).absolute()
        self.subdir = subdir
        self.workspace_root = self.workdir / subdir
        self.state_dir = self.workspace_root / STATE_DIR

    def prepare(self, job: JobRecord) -> Checkout:
        if not (self.workdir / ".git").exists():
            self.workdir.mkdir(parents=True, exist_ok=True)
            git.git(self.workdir, "init", "--quiet")
            git.git(self.workdir, "remote", "add", "origin", self.remote_url)
        try:
            git.rev_parse(self.workdir, job.ref)
        except GitError:
            git.git(self.workdir, "fetch", "--quiet", "origin", "+refs/heads/*:refs/remotes/origin/*")
        git.git(self.workdir, "checkout", "--quiet", "--detach", job.ref)
        return Checkout(worktree=self.workdir, workspace=self.workdir / self.subdir,
                        commit=git.rev_parse(self.workdir, "HEAD"))

    def push_branch(self, co: Checkout, branch: str, commit: str) -> None:
        git.git(co.worktree, "push", "--quiet", "--force", "origin", f"{commit}:refs/heads/{branch}")
        co.remote = "origin"

    def cleanup(self, co: Checkout, *, keep: bool) -> None:
        return None

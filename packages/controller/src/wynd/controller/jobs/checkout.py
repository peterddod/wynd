"""Job checkout backends (PLAN §3.18; `$DRAFTS/06 §6.3`). Stub; CTL-JOBS.

Local: `git worktree add --detach .wynd/jobs/<id>/checkout-<attempt> <ref>` under the git lock (worktrees share refs,
so a result branch is published with `update-ref`). Kube: clone, and publish with
`git push -f origin <sha>:refs/heads/<branch>`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from wynd.process.jobs import JobRecord


@dataclass
class Checkout:
    worktree: Path                  # git toplevel of the job checkout (detached at the job ref)
    workspace: Path                 # worktree / job.workspace_rel
    commit: str
    remote: str | None = None       # set once a branch was pushed to a remote (integration fetches it first)


class CheckoutBackend(Protocol):
    def prepare(self, job: JobRecord) -> Checkout: ...
    def push_branch(self, co: Checkout, branch: str, commit: str) -> None: ...
    def cleanup(self, co: Checkout, *, keep: bool) -> None: ...


class WorktreeCheckout:
    """Local runners: a detached worktree per attempt under `.wynd/jobs/<id>/`."""

    def __init__(self, workspace_root: Path, state_dir: Path) -> None:
        raise NotImplementedError("PLAN §3.18")

    def prepare(self, job: JobRecord) -> Checkout:
        raise NotImplementedError("PLAN §3.18")

    def push_branch(self, co: Checkout, branch: str, commit: str) -> None:
        raise NotImplementedError("PLAN §3.18")

    def cleanup(self, co: Checkout, *, keep: bool) -> None:
        raise NotImplementedError("PLAN §3.18")


class CloneCheckout:
    """Kube pods: clone the remote, push result branches back to it."""

    def __init__(self, remote_url: str, workdir: Path, subdir: str) -> None:
        raise NotImplementedError("PLAN §3.18")

    def prepare(self, job: JobRecord) -> Checkout:
        raise NotImplementedError("PLAN §3.18")

    def push_branch(self, co: Checkout, branch: str, commit: str) -> None:
        raise NotImplementedError("PLAN §3.18")

    def cleanup(self, co: Checkout, *, keep: bool) -> None:
        raise NotImplementedError("PLAN §3.18")

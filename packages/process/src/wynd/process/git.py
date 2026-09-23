"""Every git primitive: closure, process HEAD, dirt, worktrees, commits, branches, integration (PLAN §3.18, §3.19;
owner PROC-GIT).

All calls are subprocess `git -C <ws_root>` with workspace-relative pathspecs (a workspace may be a subdirectory of
its repository). `IntegrationResult` is the W0-complete contract; `integrate` follows `$DRAFTS/04 §13.8` with the
per-commit rule over the union closure (base, target tip, branch tip).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from .jobs import JobKind
    from .workspace import Workspace


class IntegrationResult(BaseModel):
    mode: Literal["fast_forward", "rebased", "pr_branch", "noop"]
    branch: str
    target: str
    head: str | None = None
    skipped_commits: int = 0
    conflicts: list[str] = []
    reason: str | None = None
    at: datetime
    pr_url: str | None = None             # always None in v1 (no PR is opened; `reason` carries the gh hint)


def git(
    cwd: Path, *args: str, input: bytes | None = None, check: bool = True, env: Mapping[str, str] | None = None
) -> str:
    raise NotImplementedError("PLAN §6.1 git.git")


def toplevel(path: Path) -> Path:
    raise NotImplementedError("PLAN §6.1 git.toplevel")


def prefix(ws_root: Path) -> str:
    raise NotImplementedError("PLAN §6.1 git.prefix")


def rev_parse(cwd: Path, ref: str) -> str:
    raise NotImplementedError("PLAN §6.1 git.rev_parse")


def current_branch(cwd: Path) -> str | None:
    raise NotImplementedError("PLAN §6.1 git.current_branch")


def is_ancestor(cwd: Path, a: str, b: str) -> bool:
    raise NotImplementedError("PLAN §6.1 git.is_ancestor")


def merge_base(cwd: Path, a: str, b: str) -> str:
    raise NotImplementedError("PLAN §6.1 git.merge_base")


def reference_closure(ws: Workspace, pid: str) -> list[str]:
    raise NotImplementedError("PLAN §3.19 reference_closure")


def closure_head(ws_root: Path, paths: Sequence[str], ref: str = "HEAD") -> str | None:
    raise NotImplementedError("PLAN §3.19 closure_head")


def dirty_paths(ws_root: Path, paths: Sequence[str] | None) -> list[str]:
    raise NotImplementedError("PLAN §3.19 dirty_paths")


def count_touching(ws_root: Path, a: str, b: str, paths: Sequence[str]) -> int:
    raise NotImplementedError("PLAN §6.1 git.count_touching")


def log_paths(ws_root: Path, paths: Sequence[str], limit: int) -> list[dict[str, Any]]:
    raise NotImplementedError("PLAN §6.1 git.log_paths")


def add_worktree(repo: Path, path: Path, sha: str) -> None:
    raise NotImplementedError("PLAN §6.1 git.add_worktree")


def remove_worktree(repo: Path, path: Path) -> None:
    raise NotImplementedError("PLAN §6.1 git.remove_worktree")


def worktree_for_branch(repo: Path, branch: str) -> Path | None:
    raise NotImplementedError("PLAN §6.1 git.worktree_for_branch")


def commit_paths(
    checkout_ws: Path, message: str, paths: Sequence[str] | None = None, *, trailers: Mapping[str, str] = {}
) -> str | None:
    raise NotImplementedError("PLAN §6.1 git.commit_paths")


def commit_only(ws_root: Path, paths: Sequence[str], message: str) -> str | None:
    raise NotImplementedError("PLAN §6.1 git.commit_only")


def merge_ff_only(cwd: Path, rev: str) -> None:
    raise NotImplementedError("PLAN §6.1 git.merge_ff_only")


def update_ref(cwd: Path, ref: str, new: str, old: str | None = None) -> None:
    raise NotImplementedError("PLAN §6.1 git.update_ref")


def result_branch(kind: JobKind, pid: str, job_id: str) -> str:
    raise NotImplementedError("PLAN §3.1 result_branch")


def set_branch(repo: Path, branch: str, sha: str, *, expected_old: str | None = None) -> None:
    raise NotImplementedError("PLAN §6.1 git.set_branch")


def delete_branch(repo: Path, branch: str) -> None:
    raise NotImplementedError("PLAN §6.1 git.delete_branch")


def commits_touching(ws_root: Path, base: str, tip: str, paths: Sequence[str]) -> list[tuple[str, str]]:
    raise NotImplementedError("PLAN §6.1 git.commits_touching")


def integrate(
    ws_root: Path, *, process_id: str, base_sha: str, branch: str, target_branch: str, scratch_dir: Path
) -> IntegrationResult:
    raise NotImplementedError("PLAN §3.18 integrate")

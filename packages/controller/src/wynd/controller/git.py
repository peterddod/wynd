"""`GitLock` (PLAN §8.1). Stub; CTL-JOBS.

A `threading.RLock` plus `fcntl.flock` on `.wynd/locks/git.lock`, used as a context manager. Every main-worktree
mutation and every worktree add/remove holds it; all git work calls `wynd.process.git`.
"""

from __future__ import annotations

from pathlib import Path


class GitLock:
    def __init__(self, path: Path) -> None:
        raise NotImplementedError("PLAN §8.1")

    def __enter__(self) -> GitLock:
        raise NotImplementedError("PLAN §8.1")

    def __exit__(self, *exc: object) -> None:
        raise NotImplementedError("PLAN §8.1")

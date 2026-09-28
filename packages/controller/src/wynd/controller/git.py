"""`GitLock` (PLAN §8.1; `$DRAFTS/06 §5.3`).

A `threading.RLock` plus `fcntl.flock` on `.wynd/locks/git.lock`, used as a context manager. Every main-worktree
mutation and every worktree add/remove holds it; all git work calls `wynd.process.git`.

Every `GitLock` on the same path in one process shares one re-entrant lock and one `flock`ed file, so a thread that
already holds the lock through one instance can take it again through another (the job checkout and the controller
context each build their own instance). Other processes are excluded by the `flock`.
"""

from __future__ import annotations

import fcntl
import threading
from pathlib import Path
from typing import IO


class _PathLock:
    """The per-process state of one lock file."""

    def __init__(self) -> None:
        self.rlock = threading.RLock()
        self.depth = 0
        self.file: IO[str] | None = None


_PATH_LOCKS: dict[str, _PathLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


class GitLock:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).absolute()
        with _PATH_LOCKS_GUARD:
            self._state = _PATH_LOCKS.setdefault(str(self.path), _PathLock())

    def __enter__(self) -> GitLock:
        state = self._state
        state.rlock.acquire()
        if state.depth == 0:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                f = open(self.path, "a")
                try:
                    fcntl.flock(f, fcntl.LOCK_EX)
                except BaseException:
                    f.close()
                    raise
            except BaseException:
                state.rlock.release()
                raise
            state.file = f
        state.depth += 1
        return self

    def __exit__(self, *exc: object) -> None:
        state = self._state
        state.depth -= 1
        if state.depth == 0:
            f, state.file = state.file, None
            if f is not None:
                fcntl.flock(f, fcntl.LOCK_UN)
                f.close()
        state.rlock.release()

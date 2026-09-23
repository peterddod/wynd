"""Process Dockerfile rendering (PLAN §6.5; owner PROC-BUILD, M2; `$DRAFTS/04 §8.5`).

A pure function of the lock (golden-testable): venvs sorted by id, wheels by filename, `system` sorted; build inputs
bind-mounted; a final `wynd-supervisor check-plan` smoke test before switching to the non-root user.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.spec.lockfiles import ProcessLock


def render_dockerfile(lock: ProcessLock) -> str:
    raise NotImplementedError("PLAN §6.5 render_dockerfile")

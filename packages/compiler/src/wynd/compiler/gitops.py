"""Git operations inside the job worktree (`$DRAFTS/05 §5.2–§5.3`). Commits always go through `ctx.commit`."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path


def restore_paths(worktree: Path, paths: Sequence[Path]) -> None:
    """Undo uncommitted changes under `paths` (`git checkout -- <p>` and `git clean -fdq -- <p>`)."""
    raise NotImplementedError("PLAN §7")


def squash_to(worktree: Path, *, base: str) -> None:
    """`git reset --soft <base>`; the caller then commits once with `ctx.commit`."""
    raise NotImplementedError("PLAN §7")

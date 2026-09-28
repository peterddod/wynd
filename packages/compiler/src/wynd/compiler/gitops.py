"""Git operations inside the job worktree (`$DRAFTS/05 §5.2–§5.3`). Commits always go through `ctx.commit`."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from wynd.process.git import git


def restore_paths(worktree: Path, paths: Sequence[Path]) -> None:
    """Undo uncommitted changes under `paths` (absolute, or relative to `worktree`): the index and the files go back
    to HEAD, and untracked files there are removed (`git reset`, `git checkout`, `git clean -fdq`)."""
    worktree = Path(worktree)
    specs = [_relative(worktree, Path(p)) for p in paths]
    if not specs:
        return
    git(worktree, "reset", "-q", "--", *specs)
    tracked = [p for p in git(worktree, "ls-tree", "-r", "-z", "--name-only", "HEAD", "--", *specs).split("\0") if p]
    if tracked:
        git(worktree, "checkout", "--", *_matching_head(specs, tracked))
    git(worktree, "clean", "-fdq", "--", *specs)


def squash_to(worktree: Path, *, base: str) -> None:
    """`git reset --soft <base>`: HEAD moves to `base` and every change since stays staged; the caller then commits
    once with `ctx.commit`."""
    git(Path(worktree), "reset", "--soft", base)


def _matching_head(specs: Sequence[str], tracked: Sequence[str]) -> list[str]:
    """The pathspecs that match something at HEAD (`git checkout` fails on a pathspec matching nothing)."""
    return [s for s in specs if s == "." or any(t == s or t.startswith(f"{s.rstrip('/')}/") for t in tracked)]


def _relative(worktree: Path, path: Path) -> str:
    if not path.is_absolute():
        return path.as_posix()
    return path.resolve().relative_to(worktree.resolve()).as_posix() or "."

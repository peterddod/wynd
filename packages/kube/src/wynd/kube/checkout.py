"""`checkout`: the git clone used by init containers and the controller Deployment (`$DRAFTS/08 §5.9`)."""

from __future__ import annotations

from pathlib import Path


def checkout(repo: str, dest: Path, *, ref: str | None = None, branch: str | None = None,
             if_missing: bool = False, lfs: bool = True) -> None:
    """Exactly one of ref (commit sha, detached) or branch (tracking checkout, for the controller's clone).
    if_missing: return immediately if dest/.git exists (a controller restart keeps its clone)."""
    raise NotImplementedError("PLAN §11")

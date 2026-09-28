"""M5 `edges.lock.yaml` synchronisation (PLAN §3.7, §6.1; owner EDGE-PROC, M5; `$DRAFTS/08 §4.1`).

`sync_edge_lock` adds default entries for new agentic branches, refreshes `check_hash` for edited checks and drops
entries whose branch no longer exists, preserving the knobs (provider, tier, thinking, retries, timeout) of surviving
entries; returns `(lock, changed)` and writes nothing (the compile job writes the file).

An agentic branch is a branch with a `check:` on a `kind: agentic` edge, up to the edge's else: branches after the
else are never taken (the validator warns, the run plan drops them), so they are never locked.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from wynd.spec.lockfiles import EdgeLockEntry, EdgesLock, branch_key, check_hash, load_edges_lock
from wynd.spec.process_doc import is_else
from wynd.spec.workspace import EDGES_LOCK_FILE

if TYPE_CHECKING:
    from wynd.spec.process_doc import Branch, ProcessDoc


def agentic_branches(doc: ProcessDoc) -> list[tuple[int, int, str, Branch]]:
    """(edge index, branch index, branch key, branch) of every agentic branch of `doc`, in document order."""
    out: list[tuple[int, int, str, Branch]] = []
    for i, edge in enumerate(doc.edges):
        if edge.kind != "agentic":
            continue
        for j, branch in enumerate(edge.to):
            if branch.check is not None:
                out.append((i, j, branch_key(edge.from_, j, branch.name), branch))
            if is_else(branch):
                break
    return out


def sync_edge_lock(process_dir: Path, doc: ProcessDoc) -> tuple[EdgesLock, bool]:
    """The lock for `doc`'s agentic branches (entries sorted by branch key) and whether it differs from
    `<process_dir>/edges.lock.yaml` (missing = empty). Raises SpecError when the existing lock does not load, so a
    hand-edited lock is never silently replaced."""
    old = load_edges_lock(Path(process_dir) / EDGES_LOCK_FILE)
    edges: dict[str, EdgeLockEntry] = {}
    for _, _, key, branch in agentic_branches(doc):
        digest = check_hash(branch.check, branch.context)
        entry = old.edges.get(key)
        edges[key] = EdgeLockEntry(check_hash=digest) if entry is None else entry.model_copy(
            update={"check_hash": digest})
    lock = EdgesLock(edges=dict(sorted(edges.items())))
    return lock, lock != old

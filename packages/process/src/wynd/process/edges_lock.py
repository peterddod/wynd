"""M5 `edges.lock.yaml` synchronisation (PLAN §3.7, §6.1; owner EDGE-PROC, M5; `$DRAFTS/08 §4.1`).

`sync_edge_lock` adds default entries for new agentic branches, refreshes `check_hash` for edited checks and drops
entries whose branch no longer exists, preserving the knobs of surviving entries; returns `(lock, changed)` and
writes nothing (the compile job writes the file). Until M5 it returns an empty lock, unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from wynd.spec.lockfiles import EdgesLock

if TYPE_CHECKING:
    from wynd.spec.process_doc import ProcessDoc


def sync_edge_lock(process_dir: Path, doc: ProcessDoc) -> tuple[EdgesLock, bool]:
    return EdgesLock(), False

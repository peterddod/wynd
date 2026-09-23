"""M5 agentic-edge validation (PLAN §3.2, §6.1; owner EDGE-PROC, M5).

Codes (`$DRAFTS/08 §4.3`, verbatim): `E-CHECK-NOT-AGENTIC`, `E-AGENTIC-NO-CHECK`, `E-CHECK-EMPTY`, `E-CHECK-CONTEXT`,
`W-AGENTIC-NO-ELSE`, `W-AGENTIC-EDGE-FAST`, `W-EDGE-LOCK-MISSING`, `W-EDGE-LOCK-STALE`, `W-EDGE-LOCK-ORPHAN`,
`W-EDGE-PROVIDER-UNKNOWN`. Until M5 this hook reports nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.spec.errors import Diagnostic
    from wynd.spec.lockfiles import EdgesLock

    from ..loader import LoadedProcess


def check_agentic_edges(lp: LoadedProcess, edges_lock: EdgesLock) -> list[Diagnostic]:
    return []

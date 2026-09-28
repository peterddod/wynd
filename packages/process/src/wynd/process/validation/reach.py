"""Reachability (PLAN §6.1, §6.3 pass 3; owner PROC-VAL).

`E209`: BFS over step keys from `entry`, `on_error` and every `finally` step (`$DRAFTS/04 §4.5`). Successors are the
step targets of every effective branch, whatever its `when:`.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

from ..workspace import diagnostic
from .structure import effective

if TYPE_CHECKING:
    from wynd.spec.errors import Diagnostic
    from wynd.spec.process_doc import ProcessDoc

AFTER_ELSE = " (only targeted by branches ignored after an else; see W-BRANCH-UNREACHABLE)"


def check_reach(pid: str, doc: ProcessDoc) -> list[Diagnostic]:
    """`doc` is the definition as written (branches after an else still present, to explain E209)."""
    if doc.entry not in doc.steps:
        return []  # E-ENTRY already reported; every step would look unreachable
    successors: dict[str, list[str]] = {}
    dropped: dict[str, set[str]] = {}
    for edge in doc.edges:
        kept = effective(edge)
        successors.setdefault(edge.source_step, []).extend(b.step for b in kept if b.step in doc.steps)
        dropped.setdefault(edge.source_step, set()).update(b.step for b in edge.to[len(kept):])

    roots = [doc.entry, doc.on_error, *(item.step for item in doc.finally_)]
    seen = {key for key in roots if key in doc.steps}
    work = deque(seen)
    while work:
        for target in successors.get(work.popleft(), []):
            if target not in seen:
                seen.add(target)
                work.append(target)

    after_else = {target for source in seen for target in dropped.get(source, ())}
    return [
        diagnostic("E209", source=doc._source, loc=("steps", key), process=pid, step=key, entry=doc.entry,
                   extra=AFTER_ELSE if key in after_else else "")
        for key in doc.steps
        if key not in seen
    ]

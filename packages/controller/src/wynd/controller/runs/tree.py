"""`render_tree` over runtime `build_tree` (PLAN §8.1; format `$DRAFTS/06 §5.10`, M5 `edge.check` line
`$DRAFTS/08 §4.7`). Stub; CTL-CORE.

One line per step node (`#n` when `run > 1`): exit, duration, usage (`model in→out tok $cost`) and
`summary.key_outputs` as `k=v` (80 chars); taken branches other than the first as `→ from[branch] name → to`; an
`error` exit adds `cause: <cause>: <message>`; `full` adds `in:`/`out:` JSON (400 chars each).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.runtime.trace import TraceTree


def render_tree(tree: TraceTree, *, full: bool = False) -> str:
    raise NotImplementedError("PLAN §8.1")

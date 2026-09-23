"""Derived process status (PLAN §8.1 status row, §15 item 62; `$DRAFTS/06 §5.5`). Stub; CTL-CORE.

Closure HEAD `H` at the working tree's `HEAD`; every per-commit input is read at `H` through
`load_workspace(root, CommitTree(root, H))`: design = `compile_state(ws_at_H, pid).design`; compiled = not design and
`tests_status(...)["status"] == "passed"`; built = `artefacts.get_build(pid, H)`; released = enabled releases from
`ctx.docs`, each evaluated at its own commit with `behind = count_touching(X, H, closure)`. Working-tree dirt is only
reported in `dirty` and never changes a flag. Never stored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.controller.controller import ControllerContext
    from wynd.controller.models import ProcessStatus
    from wynd.process.workspace import Workspace


def derive_status(ctx: ControllerContext, ws: Workspace, pid: str) -> ProcessStatus:
    raise NotImplementedError("PLAN §8.1")

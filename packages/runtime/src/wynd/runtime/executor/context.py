"""Pull-based structured context for agentic steps and edge checks (SPEC §3.7, PLAN §5.4, §15 item 26):
`full_trace` = this instance's history; other entries read the scope; unavailable -> null."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.runtime.executor.instance import Instance, RunCtx


def assemble_context(entries: list[str], instance: Instance, ctx: RunCtx) -> dict[str, Any]:
    raise NotImplementedError("PLAN §5.4")

"""Chat tools (PLAN §8.1 chat row, §15 item 52; `$DRAFTS/06 §7.4`). Stub; CTL-CHAT.

Read tools take any process; write tools (`edit_design`, `edit_proto`, `start_compile`, `start_build`) exist only when
`acting_on` is set and are closures bound to it. Built with `wynd.runtime.tools.handles_for`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.controller.chat.engine import TurnState
    from wynd.controller.controller import Controller
    from wynd.runtime.providers.types import ToolHandle


def build_tools(ctl: Controller, acting_on: str | None, turn: TurnState) -> list[ToolHandle]:
    """The tool handles for one turn."""
    raise NotImplementedError("PLAN §8.1")

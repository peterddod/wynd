"""CLI state and the lazily built `Controller` (PLAN §9; `$DRAFTS/06 §9.1`).

Commands call `context.get_controller(ctx)` through this module, so tests can replace it with a controller over
fake backends.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from wynd.controller import Controller


@dataclass
class CliState:
    workspace: Path | None = None             # --workspace/-C (or WYND_WORKSPACE)
    controller: Controller | None = None      # built on first use by get_controller


def get_controller(ctx: typer.Context) -> Controller:
    """`Controller.open(state.workspace)` on first use, cached on the `CliState` and closed with the root context.
    `NotAWorkspace` (exit 3) when no `wynd.yaml` is found."""
    from wynd.controller import Controller

    state = ctx.ensure_object(CliState)
    if state.controller is None:
        state.controller = Controller.open(state.workspace)
        ctx.find_root().call_on_close(state.controller.close)
    return state.controller

"""CLI state and the lazily built `Controller` (PLAN §9; `$DRAFTS/06 §9.1`). `get_controller` is a stub; CLI-M1."""

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
    """`Controller.open(state.workspace)` on first use, cached on the `CliState`."""
    raise NotImplementedError("PLAN §9")

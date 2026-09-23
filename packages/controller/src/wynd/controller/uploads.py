"""`UploadService` (PLAN §3.21 amendment 7; `$DRAFTS/06 §5.10`). Stub; CTL-DESIGN.

Stores `<state_dir>/uploads/<sha256>/<basename(filename)>` and returns that absolute path; local runs read it as is.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.controller.controller import Controller, ControllerContext


class UploadService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl

    def put(self, filename: str, data: bytes) -> Path:
        raise NotImplementedError("PLAN §3.21")

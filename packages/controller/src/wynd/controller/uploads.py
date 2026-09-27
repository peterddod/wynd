"""`UploadService` (PLAN §3.21 amendment 7; `$DRAFTS/06 §5.10`).

Stores `<state_dir>/uploads/<sha256 of the content>/<basename(filename)>` and returns that absolute path; local runs
read it as is, image and release runs send it as a run-API file (`runs/image.py`). Storing the same content under the
same name again returns the existing path.
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from wynd.controller.errors import Invalid

if TYPE_CHECKING:
    from wynd.controller.controller import Controller, ControllerContext

UPLOADS_DIR = "uploads"


class UploadService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl

    def put(self, filename: str, data: bytes) -> Path:
        name = PurePosixPath(filename.replace("\\", "/")).name
        if name in ("", ".", "..") or "\0" in name:
            raise Invalid(f"{filename!r} is not a file name", hint="send the file's name in X-Wynd-Filename")
        path = self.ctx.state_dir / UPLOADS_DIR / hashlib.sha256(data).hexdigest() / name
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        return path.absolute()

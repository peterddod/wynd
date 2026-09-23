"""The web bundle (PLAN §3.21; `$DRAFTS/06 §8.3` "Static web", `$DRAFTS/07 §3.5`). Stub; CTL-API.

`web_dist_dir`: the explicit argument, else `$WYND_WEB_DIST`, else the packaged `wynd/controller/web_dist`; the first
that contains `index.html`. `mount_web` is registered after every `/api` and `/hooks` route; without a bundle `GET /`
serves a "Web UI not built" page.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def web_dist_dir(explicit: Path | None = None) -> Path | None:
    raise NotImplementedError("PLAN §3.21")


def mount_web(app: FastAPI, dist: Path | None) -> None:
    raise NotImplementedError("PLAN §3.21")

"""`wynd serve-api`: the controller HTTP API (PLAN §3.21; `$DRAFTS/06 §8`).

Server defaults `127.0.0.1:8780`; optional bearer auth `WYND_API_TOKEN` (streams may pass `?access_token=`); CORS for
`http://localhost:5173`/`http://127.0.0.1:5173` (`serve` reads `WYND_CORS_ORIGINS` when `cors_origins` is None); one
uvicorn worker. The app internals are in `api/app.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

    from wynd.controller.controller import Controller


def create_app(
    ctl: Controller,
    *,
    web_dist: Path | None = None,
    api_token: str | None = None,
    cors_origins: Sequence[str] = ("http://localhost:5173", "http://127.0.0.1:5173"),
    scheduler: bool = True,
) -> FastAPI:
    from wynd.controller.api.app import build_app

    return build_app(ctl, web_dist=web_dist, api_token=api_token, cors_origins=cors_origins, scheduler=scheduler)


def serve(
    ctl: Controller,
    *,
    host: str = "127.0.0.1",
    port: int = 8780,
    web_dist: Path | None = None,
    api_token: str | None = None,
    cors_origins: Sequence[str] | None = None,
    scheduler: bool = True,
    log_level: str = "info",
) -> None:
    """Blocks until interrupted (`uvicorn.run`, one worker)."""
    import uvicorn

    from wynd.controller.api.app import build_app

    app = build_app(ctl, web_dist=web_dist, api_token=api_token, cors_origins=cors_origins, scheduler=scheduler)
    uvicorn.run(app, host=host, port=port, log_level=log_level)

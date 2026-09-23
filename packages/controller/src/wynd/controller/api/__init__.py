"""`wynd serve-api`: the controller HTTP API (PLAN §3.21; `$DRAFTS/06 §8`). Stub; CTL-API.

Server defaults `127.0.0.1:8780`; optional bearer auth `WYND_API_TOKEN` (streams may pass `?access_token=`); CORS for
`http://localhost:5173`/`http://127.0.0.1:5173`; one uvicorn worker.
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
    raise NotImplementedError("PLAN §3.21")


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
    raise NotImplementedError("PLAN §3.21")

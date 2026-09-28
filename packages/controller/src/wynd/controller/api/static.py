"""The web bundle (PLAN §3.21; `$DRAFTS/06 §8.3` "Static web", `$DRAFTS/07 §3.5`).

`web_dist_dir`: the explicit directory (`create_app`'s `web_dist`, else `$WYND_WEB_DIST`, resolved by `api/app.py`),
else the packaged `wynd/controller/web_dist` written by `npm --prefix packages/web run build`; the first that contains
`index.html`. `mount_web` is registered after every `/api` and `/hooks` route; without a bundle `GET /` serves a
"Web UI not built" page.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

if TYPE_CHECKING:
    from fastapi import FastAPI

PACKAGED = Path(__file__).resolve().parents[1] / "web_dist"
NOT_BUILT = (
    "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>wynd</title></head><body>"
    "<h1>Web UI not built</h1><p>run <code>npm --prefix packages/web ci &amp;&amp; npm --prefix packages/web run build"
    "</code>, then restart <code>wynd serve-api</code>. The API is served under <code>/api</code>.</p></body></html>"
)


def web_dist_dir(explicit: Path | None = None) -> Path | None:
    for candidate in (explicit, PACKAGED):
        if candidate is not None and (Path(candidate) / "index.html").is_file():
            return Path(candidate)
    return None


def mount_web(app: FastAPI, dist: Path | None) -> None:
    if dist is not None:
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
        return
    app.add_api_route("/", _not_built, methods=["GET"], include_in_schema=False)


def _not_built() -> HTMLResponse:
    return HTMLResponse(NOT_BUILT)

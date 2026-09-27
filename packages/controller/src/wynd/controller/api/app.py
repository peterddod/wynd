"""The FastAPI app factory internals: auth, CORS, error envelope, lifespan, router order (PLAN §3.21;
`$DRAFTS/06 §8.1`).

Settings come from the controller's environ (`ctl.ctx.env`): `WYND_API_TOKEN` when no `api_token` is given,
`WYND_CORS_ORIGINS` (comma-separated) when `cors_origins` is None, `WYND_WEB_DIST` when no `web_dist` is given.
Route handlers reach the controller through `Ctl` (`app.state.ctl`).

- **Auth:** with a token, every `/api/*` route except `/api/health` and `/api/oauth/callback` needs
  `Authorization: Bearer <token>`; the event streams (`…/events`) may pass `?access_token=` instead. `/hooks/*` checks
  release secrets itself; the static bundle is public.
- **Errors:** `WyndError` -> `{"error": {"code", "message", "details", "hint"}}` with its HTTP status; `Ctl` holds each
  request inside `errors.translated()`, so a process/runtime error a handler lets through leaves as a `WyndError`;
  request validation -> 422 `invalid`; Starlette HTTP errors (unknown path, wrong method) keep their status with a
  code such as `not_found`.
- **Lifespan:** startup starts the trigger backend (only with `scheduler`), reconciles it with the releases and warms
  every enabled release; shutdown stops the triggers and closes the controller.
- **Router order:** `optimise.router` and every suffixed `{process_id:path}` route come before
  `GET /api/processes/{process_id:path}`; the static web bundle is mounted last.
"""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, QueryParams
from starlette.exceptions import HTTPException

from wynd.controller.controller import Controller
from wynd.controller.errors import WyndError, translated

DEFAULT_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
OPEN_PATHS = frozenset({"/api/health", "/api/oauth/callback"})
HTTP_CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
    413: "too_large", 415: "unsupported_media_type", 422: "invalid",
}


class TooLarge(WyndError):
    code = "too_large"
    http = 413


class UnsupportedMediaType(WyndError):
    code = "unsupported_media_type"
    http = 415


async def get_ctl(request: Request) -> AsyncIterator[Controller]:
    """A handler's exception is thrown in at the `yield`; `translated()` re-raises a library error as a `WyndError`."""
    with translated():
        yield request.app.state.ctl


Ctl = Annotated[Controller, Depends(get_ctl)]


def build_app(
    ctl: Controller,
    *,
    web_dist: Path | None,
    api_token: str | None,
    cors_origins: Sequence[str] | None,
    scheduler: bool,
) -> FastAPI:
    from wynd.controller import __version__
    from wynd.controller.api import (
        routes_chats,
        routes_jobs,
        routes_meta,
        routes_processes,
        routes_registries,
        routes_releases,
        routes_runs,
    )
    from wynd.controller.api.static import mount_web, web_dist_dir
    from wynd.controller.optimise import router as optimise_router

    env = ctl.ctx.env
    token = api_token or env.get("WYND_API_TOKEN") or None
    if cors_origins is None:
        configured = [o.strip() for o in env.get("WYND_CORS_ORIGINS", "").split(",") if o.strip()]
        cors_origins = configured or DEFAULT_ORIGINS
    dist = web_dist if web_dist is not None else (Path(env["WYND_WEB_DIST"]) if env.get("WYND_WEB_DIST") else None)

    app = FastAPI(title="wynd", version=__version__, lifespan=_lifespan(ctl, scheduler),
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.ctl = ctl
    app.add_exception_handler(WyndError, _wynd_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(Exception, _internal_error)

    for router in (routes_meta.router, routes_registries.router, routes_jobs.router, optimise_router,
                   routes_processes.router, routes_runs.router, routes_chats.router, routes_releases.router):
        app.include_router(router)
    mount_web(app, web_dist_dir(dist))

    if token:
        app.add_middleware(TokenAuth, token=token)
    app.add_middleware(CORSMiddleware, allow_origins=list(cors_origins), allow_methods=["*"], allow_headers=["*"],
                       allow_credentials=False)
    return app


def error_response(status: int, code: str, message: str, details: Any = None, hint: str | None = None,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    body = {"error": {"code": code, "message": message, "details": jsonable_encoder(details), "hint": hint}}
    return JSONResponse(body, status_code=status, headers=headers)


class TokenAuth:
    """Pure ASGI middleware (streaming responses pass through untouched)."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        guarded = scope["type"] == "http" and path.startswith("/api/") and path not in OPEN_PATHS
        presented = _presented_token(scope, path) if guarded else None
        if guarded and (presented is None or not hmac.compare_digest(presented.encode(), self.token)):
            response = error_response(401, "unauthorized", "missing or invalid API token",
                                      hint="send `Authorization: Bearer <token>` (the WYND_API_TOKEN of serve-api)")
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _presented_token(scope: dict, path: str) -> str | None:
    """The bearer token, or for an event stream the `access_token` query parameter."""
    scheme, _, value = Headers(scope=scope).get("authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        return value.strip()
    if path.endswith("/events"):
        return QueryParams(scope.get("query_string", b"").decode("latin-1")).get("access_token")
    return None


def _lifespan(ctl: Controller, scheduler: bool):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await run_in_threadpool(_startup, ctl, scheduler)
        try:
            yield
        finally:
            await run_in_threadpool(_shutdown, ctl, scheduler)

    return lifespan


def _startup(ctl: Controller, scheduler: bool) -> None:
    if scheduler:
        ctl.ctx.triggers.start(ctl)
    ctl.ctx.triggers.reconcile(ctl.releases.list())
    ctl.releases.warm_all()


def _shutdown(ctl: Controller, scheduler: bool) -> None:
    try:
        if scheduler:
            ctl.ctx.triggers.stop()
    finally:
        ctl.close()


async def _wynd_error(request: Request, exc: WyndError) -> JSONResponse:
    return error_response(exc.http, exc.code, exc.message, exc.details, exc.hint)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    summary = "; ".join(f"{'.'.join(str(part) for part in e.get('loc', ()))}: {e.get('msg', '')}" for e in errors[:3])
    return error_response(422, "invalid", f"invalid request: {summary}" if summary else "invalid request", errors)


async def _http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return error_response(exc.status_code, HTTP_CODES.get(exc.status_code, "error"), str(exc.detail),
                          headers=dict(exc.headers or {}))


async def _internal_error(request: Request, exc: Exception) -> JSONResponse:
    return error_response(500, "internal", str(exc) or type(exc).__name__)

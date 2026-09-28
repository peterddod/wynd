"""Registry routes: MCP (+ OAuth start/callback), providers, image registries (PLAN §3.21 amendment 8;
`$DRAFTS/06 §8.3` route 10 and the (+) registry routes).

OAuth: `start` registers `<request base>/api/oauth/callback` as the redirect URI. The callback needs no bearer token
(the single-use state is the credential): it redirects (302) to the start's `return_to` with `mcp=<name>&ok=1`, or,
without one, answers a small HTML page; a refused or failed sign-in is an HTML page with the reason.
"""

from __future__ import annotations

import html
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from wynd.controller.api.app import Ctl
from wynd.controller.api.models_web import OAuthStart, OAuthStartRequest, ProviderAddRequest
from wynd.controller.errors import WyndError
from wynd.controller.models import ImageRegistryEntry, McpServerEntry

CALLBACK = "/api/oauth/callback"

router = APIRouter()


@router.get("/api/registries/mcp")
def list_mcp(ctl: Ctl):
    return {"items": ctl.registries.list_mcp()}


@router.post("/api/registries/mcp", status_code=201)
def add_mcp(body: McpServerEntry, ctl: Ctl):
    return ctl.registries.add_mcp(body)


@router.delete("/api/registries/mcp/{name}")
def remove_mcp(name: str, ctl: Ctl):
    return ctl.registries.remove_mcp(name)


@router.post("/api/registries/mcp/{name}/oauth/start")
def oauth_start(name: str, request: Request, ctl: Ctl, body: OAuthStartRequest | None = None):
    redirect_uri = str(request.base_url).rstrip("/") + CALLBACK
    return_to = None if body is None else body.return_to
    return OAuthStart(authorize_url=ctl.registries.oauth_start(name, redirect_uri=redirect_uri, return_to=return_to))


@router.get(CALLBACK)
def oauth_callback(ctl: Ctl, state: str = "", code: str = "", error: str | None = None,
                   error_description: str | None = None):
    if error is not None:
        return _page(400, "Sign-in failed", f"The authorization server answered {error}: {error_description or ''}")
    try:
        entry, return_to = ctl.registries.oauth_complete(state, code)
    except WyndError as err:
        return _page(err.http, "Sign-in failed", " ".join(filter(None, [err.message, err.hint])))
    if return_to:
        return RedirectResponse(_with_query(return_to, {"mcp": entry.name, "ok": "1"}), status_code=302)
    return _page(200, "Signed in", f"MCP server {entry.name} is connected. You can close this window.")


@router.get("/api/providers")
def list_providers(ctl: Ctl):
    return {"providers": ctl.registries.list_providers()}


@router.post("/api/providers", status_code=201)
def add_provider(body: ProviderAddRequest, ctl: Ctl):
    return ctl.registries.add_provider(body.name, tiers=body.tiers)


@router.delete("/api/providers/{name}")
def remove_provider(name: str, ctl: Ctl):
    return ctl.registries.remove_provider(name)


@router.get("/api/registries/images")
def list_image_registries(ctl: Ctl):
    return {"items": ctl.registries.list_image_registries()}


@router.post("/api/registries/images", status_code=201)
def add_image_registry(body: ImageRegistryEntry, ctl: Ctl):
    return ctl.registries.add_image_registry(body)


@router.delete("/api/registries/images/{name}")
def remove_image_registry(name: str, ctl: Ctl):
    return ctl.registries.remove_image_registry(name)


def _with_query(url: str, params: dict[str, str]) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(query="&".join(filter(None, [parts.query, urlencode(params)]))))


def _page(status: int, title: str, text: str) -> HTMLResponse:
    body = (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>wynd: {html.escape(title)}"
            f"</title></head><body><h1>{html.escape(title)}</h1><p>{html.escape(text)}</p></body></html>")
    return HTMLResponse(body, status_code=status)

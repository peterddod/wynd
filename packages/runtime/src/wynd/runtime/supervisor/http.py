"""The run API HTTP handler: routes, SSE writer, bearer auth (PLAN §3.17; `$DRAFTS/03 §13.3, §13.5`)."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler

ROUTES: list[tuple[str, str]] = [
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("GET", "/v1/info"),
    ("POST", "/v1/runs"),
    ("GET", "/v1/runs"),
    ("GET", "/v1/runs/{id}"),
    ("GET", "/v1/runs/{id}/outputs"),
    ("GET", "/v1/runs/{id}/events"),
]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        raise NotImplementedError("PLAN §3.17")

    def do_POST(self) -> None:
        raise NotImplementedError("PLAN §3.17")

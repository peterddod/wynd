"""The run API HTTP handler: routes, SSE writer, bearer auth (PLAN §3.17; `$DRAFTS/03 §13.3, §13.5`).

Errors are `{"error": {"code", "message", "details"}}`. `/healthz` and `/readyz` are exempt from the bearer token.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel, ValidationError

from wynd.runtime.supervisor.runs import RunApiRefusal
from wynd.runtime.supervisor.schema import RunRequest

if TYPE_CHECKING:
    from wynd.runtime.supervisor.runs import RunManager

log = logging.getLogger("wynd.supervisor.http")

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

MAX_WAIT_S = 60.0
PING_INTERVAL_S = 15.0
DEFAULT_LIST_LIMIT = 50


class RunApiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], manager: RunManager, *, token: str | None = None) -> None:
        super().__init__(address, Handler)
        self.manager = manager
        self.token = token or None


def make_server(manager: RunManager, *, host: str, port: int, token: str | None = None) -> RunApiServer:
    """Bind (port 0 picks a free port: `server.server_address[1]`); the caller runs `serve_forever`."""
    return RunApiServer((host, port), manager, token=token)


def match_route(method: str, path: str) -> tuple[str, str | None] | None:
    """`(route path, run id)` for a known route; None when the path is unknown. Method mismatches are the caller's."""
    parts = path.rstrip("/").split("/")[1:] if path != "/" else []
    match parts:
        case ["healthz"] | ["readyz"] | ["v1", "info"] | ["v1", "runs"]:
            return "/" + "/".join(parts), None
        case ["v1", "runs", run_id]:
            return "/v1/runs/{id}", run_id
        case ["v1", "runs", run_id, ("outputs" | "events") as tail]:
            return f"/v1/runs/{{id}}/{tail}", run_id
        case _:
            return None


class Handler(BaseHTTPRequestHandler):
    server: RunApiServer
    server_version = "wynd-supervisor"

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("%s " + format, self.address_string(), *args)

    # --- dispatch -------------------------------------------------------------------------------------------------------

    def _handle(self, method: str) -> None:
        url = urlsplit(self.path)
        query = {key: values[-1] for key, values in parse_qs(url.query).items()}
        found = match_route(method, url.path)
        try:
            if found is None:
                raise RunApiRefusal(404, "not_found", f"no route {url.path}")
            route, run_id = found
            if (method, route) not in ROUTES:
                raise RunApiRefusal(405, "method_not_allowed", f"{method} is not allowed on {route}")
            if route.startswith("/v1/"):
                self._authorise()
            self._route(route, run_id, query)
        except RunApiRefusal as err:
            self._error(err)
        except (BrokenPipeError, ConnectionResetError):
            log.debug("client went away during %s %s", method, url.path)

    def _route(self, route: str, run_id: str | None, query: dict[str, str]) -> None:
        manager = self.server.manager
        match route:
            case "/healthz":
                self._json(200, {"status": "ok"})
            case "/readyz":
                self._json(*manager.readiness())
            case "/v1/info":
                self._json(200, manager.info)
            case "/v1/runs" if self.command == "POST":
                self._submit()
            case "/v1/runs":
                limit = _number(query, "limit", DEFAULT_LIST_LIMIT, int)
                runs = manager.list(status=query.get("status") or None, limit=max(0, limit))
                self._json(200, {"runs": [run.model_dump(mode="json") for run in runs]})
            case "/v1/runs/{id}":
                run = self._run(run_id)
                wait = min(max(_number(query, "wait", 0.0, float), 0.0), MAX_WAIT_S)
                if wait:
                    run.wait_terminal(wait)
                self._json(200, manager.view(run))
            case "/v1/runs/{id}/outputs":
                run = self._run(run_id)
                if not run.terminal:
                    raise RunApiRefusal(409, "not_finished", f"run {run_id} is {run.status}")
                self._json(200, {"exit": run.exit, "outputs": run.outputs, "error": run.error})
            case "/v1/runs/{id}/events":
                run = self._run(run_id)
                after = query.get("after") or self.headers.get("Last-Event-ID")
                self._stream(run, _number({"after": after}, "after", -1, int) if after else -1)

    def _submit(self) -> None:
        manager = self.server.manager
        length = int(self.headers.get("Content-Length") or 0)
        if length > manager.max_body_mb * 1024 * 1024:
            _discard(self.rfile, length)
            raise RunApiRefusal(413, "too_large", f"request body exceeds {manager.max_body_mb} MB")
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as err:
            raise RunApiRefusal(400, "bad_request", f"request body is not JSON: {err}") from None
        try:
            request = RunRequest.model_validate(data)
        except ValidationError as err:
            raise RunApiRefusal(422, "invalid_request", "invalid run request",
                                json.loads(err.json(include_url=False))) from None
        created, run = manager.submit(request)
        self._json(202 if created else 200, run, headers={"Location": run.links.self})

    def _run(self, run_id: str | None) -> Any:
        run = self.server.manager.get(run_id or "")
        if run is None:
            raise RunApiRefusal(404, "not_found", f"no run {run_id}")
        return run

    def _authorise(self) -> None:
        token = self.server.token
        if token is None:
            return
        presented = self.headers.get("Authorization", "")
        scheme, _, value = presented.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(value.strip().encode(), token.encode()):
            raise RunApiRefusal(401, "unauthorized", "missing or invalid bearer token")

    # --- writers --------------------------------------------------------------------------------------------------------

    def _json(self, status: int, body: Any, *, headers: dict[str, str] | None = None) -> None:
        if isinstance(body, BaseModel):
            body = body.model_dump(mode="json")
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if status == 401:
            self.send_header("WWW-Authenticate", "Bearer")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _error(self, err: RunApiRefusal) -> None:
        self._json(err.status, {"error": {"code": err.code, "message": err.message, "details": err.details}})

    def _stream(self, run: Any, after: int) -> None:
        """Replay the buffer after `after`, follow live events, end with `end` and close (no Content-Length)."""
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self._write(b": connected\n\n")
        last_write = time.monotonic()
        while True:
            events, ended = run.wait_events(after, PING_INTERVAL_S)
            for index, event, data in events:
                frame = f"id: {index}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                self._write(frame.encode())
                after = index
                last_write = time.monotonic()
            if ended:
                return
            if time.monotonic() - last_write >= PING_INTERVAL_S:
                self._write(b": ping\n\n")
                last_write = time.monotonic()

    def _write(self, data: bytes) -> None:
        self.wfile.write(data)
        self.wfile.flush()


def _discard(stream: Any, length: int) -> None:
    while length > 0:
        chunk = stream.read(min(length, 1 << 16))
        if not chunk:
            return
        length -= len(chunk)


def _number(query: dict[str, str | None], name: str, default: Any, kind: type) -> Any:
    raw = query.get(name)
    if raw is None or raw == "":
        return default
    try:
        return kind(raw)
    except ValueError:
        raise RunApiRefusal(400, "bad_request", f"query parameter {name} must be a number") from None

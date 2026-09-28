"""A stdlib fake MCP server for tests and offline demos: HTTP (JSON and SSE bodies) and stdio
(`python -m wynd.runtime.mcp.fake_server`; spike `docs/design/spikes/fake_mcp_http.py`).

    python -m wynd.runtime.mcp.fake_server                      # stdio
    python -m wynd.runtime.mcp.fake_server --http [--port N] [--sse] [--token T] [--page-size N]
                                                                # prints the URL on the first stdout line

`--tools` replaces the catalogue (a JSON list of MCP tool objects, inline or a file path). Tool behaviour by name:
`get_issue` returns an issue as JSON text; `list_issues` returns `structuredContent` only; `delete_repo` returns an
`isError` result; `crash` kills a stdio server (exit 3) and answers HTTP 500; `read_env` returns the server's value
of the env var `arguments["name"]`; any other catalogue tool echoes its arguments as JSON text. In-process:
`serve_http(...)` returns a running `FakeHttpServer` (a context manager).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SESSION_ID = "fake-session-1"

DEFAULT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_issue",
        "description": "Get a GitHub issue by number.",
        "inputSchema": {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "list_issues",
        "description": "List the issues of the repository.",
        "inputSchema": {"type": "object", "properties": {"state": {"type": "string"}}},
        "annotations": {"idempotentHint": True},
    },
    {
        "name": "delete_repo",
        "description": "Delete a repository (must never be allowed).",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
        "annotations": {"destructiveHint": True},
    },
    {
        "name": "crash",
        "description": "Kill the server (tests of a dying server).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

LOG_NOTIFICATION = {"jsonrpc": "2.0", "method": "notifications/message", "params": {"level": "info", "data": "working"}}


class Crash(Exception):
    """The `crash` tool was called."""


class FakeMcp:
    """Transport-independent protocol state: the catalogue, pagination and every message received."""

    def __init__(self, tools: list[dict[str, Any]] | None = None, *, page_size: int | None = None) -> None:
        self.tools = list(DEFAULT_TOOLS if tools is None else tools)
        self.page_size = page_size
        self.messages: list[dict[str, Any]] = []

    def handle(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        """The JSON-RPC response to `msg` (None for a notification); raises `Crash`."""
        self.messages.append(msg)
        if "id" not in msg:
            return None
        params = msg.get("params") or {}
        match msg.get("method"):
            case "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2025-11-25"),
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "wynd-fake-mcp", "version": "0.1.0"},
                }
            case "tools/list":
                result = self._list(params.get("cursor"))
            case "tools/call":
                return self._call(msg["id"], params)
            case "ping":
                result = {}
            case _:
                return _error(msg["id"], -32601, f"method not found: {msg.get('method')}")
        return {"jsonrpc": "2.0", "id": msg["id"], "result": result}

    def _list(self, cursor: str | None) -> dict[str, Any]:
        start = int(cursor or 0)
        size = self.page_size or len(self.tools)
        result: dict[str, Any] = {"tools": self.tools[start:start + size]}
        if start + size < len(self.tools):
            result["nextCursor"] = str(start + size)
        return result

    def _call(self, id: Any, params: dict[str, Any]) -> dict[str, Any]:
        name, arguments = params.get("name"), params.get("arguments") or {}
        if name not in {t["name"] for t in self.tools}:
            return _error(id, -32602, f"unknown tool: {name}")
        match name:
            case "get_issue":
                issue = {"number": arguments.get("number"), "state": "open", "title": "Login page broken"}
                result: dict[str, Any] = {"content": [{"type": "text", "text": json.dumps(issue)}], "isError": False}
            case "list_issues":
                result = {"content": [], "structuredContent": {"issues": [{"number": 42, "state": "open"}]}}
            case "delete_repo":
                result = {"content": [{"type": "text", "text": "forbidden"}], "isError": True}
            case "crash":
                raise Crash()
            case "read_env":
                result = {"content": [{"type": "text", "text": os.environ.get(arguments.get("name", ""), "")}]}
            case _:
                result = {"content": [{"type": "text", "text": json.dumps(arguments, sort_keys=True)}]}
        return {"jsonrpc": "2.0", "id": id, "result": result}


def _error(id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}}


class FakeHttpServer:
    """Streamable HTTP on 127.0.0.1 in a daemon thread. `requests` records `(method, headers, body)` per request
    (header names lower-cased). With `token`, every request needs `Authorization: Bearer <token>` (else 401); after
    `initialize`, every POST needs the session id the server issued (else 400)."""

    def __init__(self, mcp: FakeMcp, *, token: str | None = None, sse: bool = False, port: int = 0) -> None:
        self.mcp = mcp
        self.token = token
        self.sse = sse
        self.requests: list[tuple[str, dict[str, str], Any]] = []
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), _handler(self))
        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/mcp"
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> FakeHttpServer:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def serve_http(tools: list[dict[str, Any]] | None = None, *, token: str | None = None, sse: bool = False,
               page_size: int | None = None, port: int = 0) -> FakeHttpServer:
    return FakeHttpServer(FakeMcp(tools, page_size=page_size), token=token, sse=sse, port=port)


def _handler(server: FakeHttpServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"null")
            server.requests.append(("POST", _lower(self.headers), body))
            if not self._authorised():
                return
            if body.get("method") != "initialize" and self.headers.get("Mcp-Session-Id") != SESSION_ID:
                return self._status(400)
            try:
                response = server.mcp.handle(body)
            except Crash:
                return self._status(500)
            if response is None:
                return self._status(202)
            self.send_response(200)
            if body.get("method") == "initialize":
                self.send_header("Mcp-Session-Id", SESSION_ID)
            if server.sse:
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for event_id, message in enumerate((LOG_NOTIFICATION, response), start=1):
                    self.wfile.write(f"id: {event_id}\nevent: message\ndata: {json.dumps(message)}\n\n".encode())
                return
            data = json.dumps(response).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_DELETE(self) -> None:
            server.requests.append(("DELETE", _lower(self.headers), None))
            if self._authorised():
                self._status(200)

        def do_GET(self) -> None:
            self._status(405)

        def _authorised(self) -> bool:
            if server.token is None or self.headers.get("Authorization") == f"Bearer {server.token}":
                return True
            self._status(401)
            return False

        def _status(self, code: int) -> None:
            self.send_response(code)
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def _lower(headers: Any) -> dict[str, str]:
    return {k.lower(): v for k, v in headers.items()}


def serve_stdio(mcp: FakeMcp) -> int:
    """Newline-delimited JSON-RPC on stdin/stdout; a log notification precedes every `tools/call` response."""
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        try:
            response = mcp.handle(msg)
        except Crash:
            os._exit(3)
        if response is None:
            continue
        if msg.get("method") == "tools/call":
            sys.stdout.write(json.dumps(LOG_NOTIFICATION) + "\n")
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
    return 0


def _load_tools(text: str) -> list[dict[str, Any]]:
    return json.loads(text if text.lstrip().startswith("[") else Path(text).read_text())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m wynd.runtime.mcp.fake_server")
    parser.add_argument("--http", action="store_true", help="serve streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--sse", action="store_true", help="answer POSTs with text/event-stream bodies")
    parser.add_argument("--token", help="require Authorization: Bearer <token>")
    parser.add_argument("--page-size", type=int, help="tools/list page size")
    parser.add_argument("--tools", help="JSON list of MCP tool objects, inline or a file path")
    args = parser.parse_args(argv)
    mcp = FakeMcp(_load_tools(args.tools) if args.tools else None, page_size=args.page_size)
    if not args.http:
        return serve_stdio(mcp)
    server = FakeHttpServer(mcp, token=args.token, sse=args.sse, port=args.port)
    print(server.url, flush=True)
    try:
        server._thread.join()
    except KeyboardInterrupt:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Stdlib MCP client over streamable HTTP (JSON and SSE bodies) and stdio, protocol `2025-11-25` (PLAN §1.5;
`$DRAFTS/03 §10.3`; spike `docs/design/spikes/mcp_client_stdlib.py`).

Only the client side of `initialize`, `notifications/initialized`, `tools/list` (with pagination) and `tools/call`.
Server->client requests and notifications (sampling, elicitation, roots, logging) are ignored.
"""

from __future__ import annotations

import contextlib
import http.client
import itertools
import json
import subprocess
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any

from wynd.runtime import __version__

PROTOCOL_VERSION = "2025-11-25"
CLIENT_INFO = {"name": "wynd", "version": __version__}


class McpError(Exception):
    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class McpAuthError(McpError):
    """HTTP 401/403 from the server."""


class HttpTransport:
    """Streamable HTTP: one POST per message; the session id and protocol version are echoed once known."""

    def __init__(self, url: str, headers: dict[str, str], timeout: float) -> None:
        self.url = url
        self.headers = dict(headers)
        self.timeout = timeout
        self.session_id: str | None = None
        self.protocol_version: str | None = None

    def send(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            headers["MCP-Protocol-Version"] = self.protocol_version
        request = urllib.request.Request(self.url, data=json.dumps(msg).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                session = resp.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
                if "id" not in msg:
                    return None
                if resp.headers.get_content_type() == "text/event-stream":
                    return _read_sse(resp, msg["id"], self.url)
                return _loads(resp.read(), self.url)
        except urllib.error.HTTPError as e:
            raise _http_error(e, self.url) from e
        except (OSError, http.client.HTTPException) as e:
            raise McpError(f"MCP server {self.url} is unreachable: {e}", transient=True) from e

    def close(self) -> None:
        """DELETE the session (best effort)."""
        if not self.session_id:
            return
        request = urllib.request.Request(self.url, headers={**self.headers, "Mcp-Session-Id": self.session_id},
                                         method="DELETE")
        with contextlib.suppress(Exception):
            urllib.request.urlopen(request, timeout=5).close()
        self.session_id = None


def _http_error(e: urllib.error.HTTPError, url: str) -> McpError:
    if e.code in (401, 403):
        return McpAuthError(f"MCP server {url} rejected the credentials (HTTP {e.code})")
    body = e.read()[:200].decode("utf-8", "replace")
    return McpError(f"HTTP {e.code} from MCP server {url}: {body}", transient=e.code == 429 or e.code >= 500)


def _read_sse(resp: Any, want_id: Any, url: str) -> dict[str, Any]:
    """Read `data:` events until the JSON-RPC response with `want_id`; other messages are ignored."""
    data: list[str] = []
    for raw in itertools.chain(resp, [b""]):
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
            continue
        if line or not data:
            continue
        obj = _loads("\n".join(data), url)
        data = []
        if obj.get("id") == want_id and ("result" in obj or "error" in obj):
            return obj
    raise McpError(f"MCP server {url} ended the SSE stream without a response")


def _loads(text: str | bytes, source: str) -> dict[str, Any]:
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise McpError(f"invalid JSON from MCP server {source}: {e}") from e
    if not isinstance(obj, dict):
        raise McpError(f"invalid JSON-RPC message from MCP server {source}: {str(obj)[:200]}")
    return obj


class StdioTransport:
    """A child process speaking newline-delimited JSON-RPC on stdin/stdout (stderr discarded)."""

    def __init__(self, command: list[str], env: Mapping[str, str]) -> None:
        self.command = list(command)
        self.protocol_version: str | None = None
        try:
            self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, env=dict(env), text=True, encoding="utf-8",
                                         bufsize=1)
        except OSError as e:
            raise McpError(f"cannot start MCP server {self.command[0]!r}: {e}") from e

    def send(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise McpError("stdio server exited") from e
        if "id" not in msg:
            return None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise McpError("stdio server exited")
            if not line.strip():
                continue
            obj = _loads(line, self.command[0])
            if obj.get("id") == msg["id"] and ("result" in obj or "error" in obj):
                return obj

    def close(self) -> None:
        """Close stdin, terminate, wait 5 s, kill."""
        with contextlib.suppress(OSError, ValueError):
            self.proc.stdin.close()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        with contextlib.suppress(OSError, ValueError):
            self.proc.stdout.close()


class McpClient:
    def __init__(self, transport: HttpTransport | StdioTransport) -> None:
        self.transport = transport
        self._ids = itertools.count(1)

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        resp = self.transport.send({"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params})
        if resp is None:
            raise McpError(f"{method}: no response")
        if "error" in resp:
            error = resp["error"] if isinstance(resp["error"], dict) else {"message": resp["error"]}
            raise McpError(f"{method} failed: {error.get('message')} (code {error.get('code')})")
        return resp["result"]

    def initialize(self) -> dict[str, Any]:
        """The handshake, then `notifications/initialized`; the server's protocol version is echoed from now on."""
        result = self._request(
            "initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO}
        )
        self.transport.protocol_version = result.get("protocolVersion", PROTOCOL_VERSION)
        self.transport.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        """Every tool, following `nextCursor` pagination."""
        tools: list[dict[str, Any]] = []
        cursor = None
        while True:
            result = self._request("tools/list", {"cursor": cursor} if cursor else {})
            tools += result.get("tools", [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """`{"content": [...], "isError": bool, "structuredContent"?: ...}`."""
        return self._request("tools/call", {"name": name, "arguments": arguments})

    def close(self) -> None:
        self.transport.close()


def tool_result_text(result: Mapping[str, Any]) -> str:
    """What the model sees for a `tools/call` result: text items verbatim, `[image omitted]`, a resource's text or
    `[resource <uri>]`; `structuredContent` as JSON when there is no text item."""
    parts: list[str] = []
    for item in result.get("content") or []:
        match item.get("type"):
            case "text":
                parts.append(item.get("text", ""))
            case "image" | "audio":
                parts.append(f"[{item['type']} omitted]")
            case "resource":
                resource = item.get("resource") or {}
                parts.append(resource["text"] if "text" in resource else f"[resource {resource.get('uri', '')}]")
            case "resource_link":
                parts.append(f"[resource {item.get('uri', '')}]")
    if not parts and result.get("structuredContent") is not None:
        return json.dumps(result["structuredContent"], ensure_ascii=False, sort_keys=True)
    return "\n".join(parts)

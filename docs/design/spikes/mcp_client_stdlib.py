"""Prototype of wynd.runtime.mcp.client: minimal stdlib MCP client.

Transports: streamable HTTP (JSON or SSE response bodies) and stdio (newline-delimited JSON-RPC).
Only: initialize, notifications/initialized, tools/list (with pagination), tools/call.
"""
from __future__ import annotations

import itertools
import json
import subprocess
import urllib.error
import urllib.request

PROTOCOL_VERSION = "2025-11-25"
CLIENT_INFO = {"name": "wynd", "version": "0.1.0"}


class McpError(Exception):
    pass


class HttpTransport:
    def __init__(self, url: str, headers: dict[str, str], timeout: float = 30.0):
        self.url, self.headers, self.timeout = url, dict(headers), timeout
        self.session_id: str | None = None
        self.protocol_version: str | None = None

    def send(self, msg: dict) -> dict | None:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            h["MCP-Protocol-Version"] = self.protocol_version
        req = urllib.request.Request(self.url, data=json.dumps(msg).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                if "id" not in msg:
                    return None
                ctype = resp.headers.get("Content-Type", "")
                if ctype.startswith("text/event-stream"):
                    return _read_sse_response(resp, msg["id"])
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise McpError(f"HTTP {e.code} from {self.url}: {e.read()[:200]!r}") from e

    def close(self) -> None:
        if self.session_id:
            h = {**self.headers, "Mcp-Session-Id": self.session_id}
            try:
                urllib.request.urlopen(urllib.request.Request(self.url, headers=h, method="DELETE"), timeout=5)
            except Exception:
                pass


def _read_sse_response(resp, want_id) -> dict:
    data_lines: list[str] = []
    for raw in resp:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line == "" and data_lines:
            obj = json.loads("\n".join(data_lines))
            data_lines = []
            if obj.get("id") == want_id and ("result" in obj or "error" in obj):
                return obj
    raise McpError("SSE stream ended without a response")


class StdioTransport:
    def __init__(self, command: list[str], env: dict[str, str] | None = None):
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1)

    def send(self, msg: dict) -> dict | None:
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        if "id" not in msg:
            return None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise McpError("stdio server exited")
            obj = json.loads(line)
            if obj.get("id") == msg["id"] and ("result" in obj or "error" in obj):
                return obj
            # server->client requests/notifications are ignored (no sampling/elicitation support)

    def close(self) -> None:
        self.proc.stdin.close()
        self.proc.terminate()
        self.proc.wait(timeout=5)


class McpClient:
    def __init__(self, transport):
        self.t = transport
        self.ids = itertools.count(1)

    def _call(self, method: str, params: dict | None = None) -> dict:
        resp = self.t.send({"jsonrpc": "2.0", "id": next(self.ids), "method": method, "params": params or {}})
        if "error" in resp:
            raise McpError(f"{method}: {resp['error']}")
        return resp["result"]

    def initialize(self) -> dict:
        res = self._call("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO})
        if isinstance(self.t, HttpTransport):
            self.t.protocol_version = res["protocolVersion"]
        self.t.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return res

    def list_tools(self) -> list[dict]:
        tools, cursor = [], None
        while True:
            res = self._call("tools/list", {"cursor": cursor} if cursor else {})
            tools += res["tools"]
            cursor = res.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self._call("tools/call", {"name": name, "arguments": arguments})

    def close(self):
        self.t.close()

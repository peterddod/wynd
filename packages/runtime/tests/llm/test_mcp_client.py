"""The stdlib MCP client against `fake_server` over HTTP (JSON and SSE bodies) and stdio (PLAN §1.5;
`$DRAFTS/03 §10.3`)."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from wynd.runtime.agentic.errors import McpConfigError
from wynd.runtime.mcp import McpServerEntry, open_client, resolve_env_refs
from wynd.runtime.mcp.client import (
    PROTOCOL_VERSION,
    HttpTransport,
    McpAuthError,
    McpClient,
    McpError,
    StdioTransport,
    tool_result_text,
)
from wynd.runtime.mcp.fake_server import DEFAULT_TOOLS, SESSION_ID, serve_http

FAKE_SERVER = [sys.executable, "-m", "wynd.runtime.mcp.fake_server"]
TOOL_NAMES = [t["name"] for t in DEFAULT_TOOLS]


def http_client(url: str, token: str | None = "t0k") -> McpClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return McpClient(HttpTransport(url, headers, timeout=5))


def stdio_client(*args: str) -> McpClient:
    return McpClient(StdioTransport([*FAKE_SERVER, *args], os.environ))


def exercise(client: McpClient) -> None:
    """The client surface every transport must support."""
    result = client.initialize()
    assert result["protocolVersion"] == PROTOCOL_VERSION
    assert result["serverInfo"]["name"] == "wynd-fake-mcp"
    assert [t["name"] for t in client.list_tools()] == TOOL_NAMES
    issue = client.call_tool("get_issue", {"number": 42})
    assert issue["isError"] is False
    assert json.loads(issue["content"][0]["text"]) == {"number": 42, "state": "open", "title": "Login page broken"}
    denied = client.call_tool("delete_repo", {"name": "wynd"})
    assert denied["isError"] is True and tool_result_text(denied) == "forbidden"


@pytest.mark.parametrize("sse", [False, True], ids=["json", "sse"])
def test_http_transport(sse):
    with serve_http(token="t0k", sse=sse) as server:
        client = http_client(server.url)
        exercise(client)
        client.close()
        methods = [(m, body and body.get("method")) for m, _, body in server.requests]
        assert methods == [("POST", "initialize"), ("POST", "notifications/initialized"), ("POST", "tools/list"),
                           ("POST", "tools/call"), ("POST", "tools/call"), ("DELETE", None)]
        for _, headers, _ in server.requests[1:]:
            assert headers["mcp-session-id"] == SESSION_ID
        assert all(h["mcp-protocol-version"] == PROTOCOL_VERSION for _, h, _ in server.requests[1:5])
        assert "mcp-session-id" not in server.requests[0][1]
        assert server.requests[0][1]["accept"] == "application/json, text/event-stream"


def test_stdio_transport():
    client = stdio_client()
    try:
        exercise(client)               # the server writes a log notification before every tools/call response
    finally:
        client.close()
    assert client.transport.proc.returncode is not None


def test_list_tools_follows_pagination():
    with serve_http(token="t0k", page_size=1) as server:
        client = http_client(server.url)
        client.initialize()
        assert [t["name"] for t in client.list_tools()] == TOOL_NAMES
        lists = [body["params"] for _, _, body in server.requests if body and body.get("method") == "tools/list"]
        assert lists == [{}, {"cursor": "1"}, {"cursor": "2"}, {"cursor": "3"}]
    client = stdio_client("--page-size", "3")
    try:
        client.initialize()
        assert [t["name"] for t in client.list_tools()] == TOOL_NAMES
    finally:
        client.close()


def test_wrong_or_missing_credentials_are_auth_errors():
    with serve_http(token="t0k") as server:
        for token in ("wrong", None):
            with pytest.raises(McpAuthError, match=r"rejected the credentials \(HTTP 401\)") as info:
                http_client(server.url, token=token).initialize()
            assert info.value.transient is False


def test_server_and_network_failures_are_transient():
    with serve_http(token="t0k") as server:
        client = http_client(server.url)
        client.initialize()
        with pytest.raises(McpError, match="HTTP 500") as info:
            client.call_tool("crash", {})
        assert info.value.transient is True
        url = server.url
    with pytest.raises(McpError, match="is unreachable") as info:
        http_client(url).initialize()
    assert info.value.transient is True


def test_json_rpc_errors_are_not_transient():
    with serve_http() as server:
        client = http_client(server.url, token=None)
        client.initialize()
        with pytest.raises(McpError, match=r"tools/call failed: unknown tool: nope \(code -32602\)") as info:
            client.call_tool("nope", {})
        assert info.value.transient is False


def test_a_dying_stdio_server():
    client = stdio_client()
    try:
        client.initialize()
        with pytest.raises(McpError, match="stdio server exited") as info:
            client.call_tool("crash", {})
        assert info.value.transient is False
        assert client.transport.proc.wait(timeout=5) == 3
        with pytest.raises(McpError, match="stdio server exited"):
            client.list_tools()
    finally:
        client.close()


def test_a_missing_stdio_command():
    with pytest.raises(McpError, match="cannot start MCP server 'no-such-mcp-server-wynd'"):
        StdioTransport(["no-such-mcp-server-wynd"], os.environ)


def test_open_client_resolves_env_references(monkeypatch):
    with serve_http(token="gh-secret") as server:
        entry = McpServerEntry(name="github", transport="http", url=server.url,
                               headers={"Authorization": "Bearer ${env:GITHUB_TOKEN}"}, auth_env=["GITHUB_TOKEN"])
        client = open_client(entry, {"GITHUB_TOKEN": "gh-secret"})
        assert [t["name"] for t in client.list_tools()] == TOOL_NAMES
        client.close()
        with pytest.raises(McpConfigError, match=r"^MCP server 'github' needs env var GITHUB_TOKEN \(listed in "):
            open_client(entry, {})
        assert [m for m, _, _ in server.requests].count("POST") == 3      # the failed open sent nothing
    read_env = [{"name": "read_env", "description": "Read an env var.", "inputSchema": {"type": "object"}}]
    stdio = McpServerEntry(name="files", transport="stdio", command=[*FAKE_SERVER, "--tools", json.dumps(read_env)],
                           env={"FAKE_TOKEN": "tok-${env:T}"})
    client = open_client(stdio, {**os.environ, "T": "x"})
    try:
        assert tool_result_text(client.call_tool("read_env", {"name": "FAKE_TOKEN"})) == "tok-x"
        assert tool_result_text(client.call_tool("read_env", {"name": "T"})) == "x"     # the caller's env is inherited
    finally:
        client.close()


def test_resolve_env_refs():
    env = {"A": "1", "B": "two", "EMPTY": ""}
    assert resolve_env_refs("${env:A}-${env:B}-${env:A}", env, server="s") == "1-two-1"
    assert resolve_env_refs("no refs, $A or ${A}", env, server="s") == "no refs, $A or ${A}"
    for name in ("MISSING", "EMPTY"):
        with pytest.raises(McpConfigError) as info:
            resolve_env_refs(f"Bearer ${{env:{name}}}", env, server="github")
        assert str(info.value) == f"MCP server 'github' needs env var {name} (listed in process.env.yaml)"


def test_registry_entry_validation():
    with pytest.raises(ValueError, match="transport http needs a url"):
        McpServerEntry(name="github", transport="http")
    with pytest.raises(ValueError, match="transport stdio needs a command"):
        McpServerEntry(name="files", transport="stdio", command=[])
    with pytest.raises(ValueError, match="must match"):
        McpServerEntry(name="GitHub", transport="http", url="https://x")
    with pytest.raises(ValueError):
        McpServerEntry(name="github", transport="http", url="https://x", token="literal secret")


def test_tool_result_text():
    assert tool_result_text({"content": [
        {"type": "text", "text": "one"},
        {"type": "image", "data": "...", "mimeType": "image/png"},
        {"type": "resource", "resource": {"uri": "file:///a.txt", "text": "file text"}},
        {"type": "resource", "resource": {"uri": "file:///b.bin", "blob": "..."}},
    ]}) == "one\n[image omitted]\nfile text\n[resource file:///b.bin]"
    assert tool_result_text({"content": [], "structuredContent": {"b": 1, "a": 2}}) == '{"a": 2, "b": 1}'
    assert tool_result_text({"content": [{"type": "text", "text": "t"}], "structuredContent": {"a": 1}}) == "t"


def test_fake_server_cli_serves_http():
    proc = subprocess.Popen([*FAKE_SERVER, "--http", "--sse", "--token", "t0k", "--tools",
                             json.dumps(DEFAULT_TOOLS[:1])], stdout=subprocess.PIPE, text=True)
    try:
        url = proc.stdout.readline().strip()
        client = http_client(url)
        client.initialize()
        assert [t["name"] for t in client.list_tools()] == ["get_issue"]
        client.close()
    finally:
        proc.terminate()
        proc.wait(timeout=5)
        proc.stdout.close()

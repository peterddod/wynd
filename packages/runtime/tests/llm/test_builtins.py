"""The builtin tool library (PLAN §5.1; `$DRAFTS/03 §9.3`), invoked through a `ToolSet` as the loop does.

`self.runtime.http` is doubled by a small urllib client with the `HttpClient` contract (non-2xx returned, network
failures raise `HttpError(response=None)`); RT-STEP builds the real one in parallel.
"""

from __future__ import annotations

import http.client
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from wynd.runtime.agentic.errors import MissingEnvVar, ToolFailure, ToolInputError
from wynd.runtime.http import HttpError
from wynd.runtime.providers.types import ToolResult
from wynd.runtime.tools import (
    http_get,
    now,
    shell,
    tool,
    web_search,
    workspace_read,
    workspace_write,
)
from wynd.runtime.tools.decorator import tool_spec
from wynd.runtime.tools.toolset import ToolSet

DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})")   # PLAN §3.16


class Trace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, type: str, **fields: Any) -> None:
        self.events.append({"type": type, **fields})


class UrllibHttp:
    """The `HttpClient.get` contract over urllib."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, *, params=None, headers=None, timeout=None) -> SimpleNamespace:
        self.requests.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        try:
            with urllib.request.urlopen(urllib.request.Request(full, headers=dict(headers or {})), timeout=5) as r:
                return SimpleNamespace(url=full, status=r.status, headers=dict(r.headers), body=r.read())
        except urllib.error.HTTPError as e:
            return SimpleNamespace(url=full, status=e.code, headers=dict(e.headers), body=e.read())
        except (OSError, http.client.HTTPException) as e:
            raise HttpError(f"GET {full} failed: {e}", response=None) from e


class ScriptedHttp:
    """Returns canned responses and records the requests."""

    def __init__(self, *responses: SimpleNamespace) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, **kw: Any) -> SimpleNamespace:
        self.requests.append({"url": url, **kw})
        return self.responses.pop(0)


def response(status: int, body: Any) -> SimpleNamespace:
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    return SimpleNamespace(url="https://x", status=status, headers={"Content-Type": "application/json"}, body=data)


def toolset(tmp_path: Path, fn: Any, *, http: Any = None, retries: int = 1) -> tuple[ToolSet, SimpleNamespace]:
    rt = SimpleNamespace(run_id="run_1", workspace=tmp_path, trace=Trace(), http=http)
    return ToolSet([tool_spec(fn, "builtin")], runtime=rt, tool_retries=retries), rt


def tools_for(tmp_path: Path, fn: Any, http: Any = None) -> ToolSet:
    return toolset(tmp_path, fn, http=http)[0]


@pytest.fixture
def no_sleep(monkeypatch) -> None:
    monkeypatch.setattr("wynd.runtime.tools.toolset.time.sleep", lambda s: None)


@pytest.fixture
def site():
    hits: Counter[str] = Counter()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            hits[self.path] += 1
            match self.path:
                case "/reset-once" if hits[self.path] == 1:
                    self.close_connection = True
                    self.connection.shutdown(2)          # drop the connection without a response
                    return
                case "/reset-once" | "/page":
                    self._reply(200, b"<h1>hello</h1>", "text/html; charset=utf-8")
                case "/big":
                    self._reply(200, b"a" * 300_000, "text/plain")
                case "/busy":
                    self._reply(503, b"try later", "text/plain")
                case _:
                    self._reply(404, b"not here", "text/plain")

        def _reply(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_address[1]}", hits=hits)
    server.shutdown()
    server.server_close()


def test_http_get_retries_a_reset_connection_once(tmp_path, site, no_sleep):
    tools, rt = toolset(tmp_path, http_get, http=UrllibHttp())
    result = json.loads(tools.invoke("http_get", {"url": site.url + "/reset-once"}).text)
    assert result == {"status": 200, "content_type": "text/html; charset=utf-8", "text": "<h1>hello</h1>",
                      "truncated": False}
    assert site.hits["/reset-once"] == 2 and rt.trace.events[0]["tries"] == 2


def test_http_get_returns_error_statuses_as_results(tmp_path, site, no_sleep):
    tools, _ = toolset(tmp_path, http_get, http=UrllibHttp())
    missing = tools.invoke("http_get", {"url": site.url + "/nowhere"})
    busy = tools.invoke("http_get", {"url": site.url + "/busy"})
    assert not missing.is_error and json.loads(missing.text)["status"] == 404
    assert json.loads(busy.text)["status"] == 503 and json.loads(busy.text)["text"] == "try later"
    assert site.hits["/busy"] == 1                                 # a status is an answer, not a transport failure


def test_http_get_truncates_the_body(tmp_path, site):
    @tool
    def probe(url: str) -> list:
        """Call the builtin directly inside a tool run (the model-facing result is capped at 100 000 chars)."""
        result = http_get(url)
        return [len(result.text), result.truncated]

    tools, _ = toolset(tmp_path, probe, http=UrllibHttp())
    assert json.loads(tools.invoke("probe", {"url": site.url + "/big"}).text) == [200_000, True]
    capped = tools_for(tmp_path, http_get, UrllibHttp()).invoke("http_get", {"url": site.url + "/big"}).text
    full = json.dumps({"content_type": "text/plain", "status": 200, "text": "a" * 200_000, "truncated": True})
    assert capped == full[:100_000] + f"…[truncated {len(full) - 100_000} chars]"


def test_http_get_unreachable_after_retry_is_a_tool_failure(tmp_path, no_sleep):
    tools, rt = toolset(tmp_path, http_get, http=UrllibHttp())
    with pytest.raises(ToolFailure, match="tool http_get failed: HttpError"):
        tools.invoke("http_get", {"url": "http://127.0.0.1:9/"})
    assert rt.trace.events[0]["tries"] == 2


def test_web_search_calls_brave_and_maps_results(tmp_path, monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "brave-key")
    brave = {"web": {"results": [
        {"title": "Wynd", "url": "https://wynd.dev", "description": "Process graphs", "age": "1d"},
        {"title": "Docs", "url": "https://wynd.dev/docs", "description": "Read me"},
    ]}}
    http = ScriptedHttp(response(200, brave))
    tools, _ = toolset(tmp_path, web_search, http=http)
    results = json.loads(tools.invoke("web_search", {"query": "wynd process", "count": 50}).text)
    assert results == [
        {"snippet": "Process graphs", "title": "Wynd", "url": "https://wynd.dev"},
        {"snippet": "Read me", "title": "Docs", "url": "https://wynd.dev/docs"},
    ]
    assert http.requests == [{
        "url": "https://api.search.brave.com/res/v1/web/search",
        "params": {"q": "wynd process", "count": "20"},
        "headers": {"Accept": "application/json", "X-Subscription-Token": "brave-key"},
        "timeout": 30,
    }]


def test_web_search_errors(tmp_path, monkeypatch, no_sleep):
    tools, _ = toolset(tmp_path, web_search, http=ScriptedHttp())
    with pytest.raises(MissingEnvVar, match="BRAVE_API_KEY is not set"):
        tools.invoke("web_search", {"query": "q"})

    monkeypatch.setenv("BRAVE_API_KEY", "brave-key")
    http = ScriptedHttp(response(503, b""), response(200, {"web": {"results": []}}))
    tools, rt = toolset(tmp_path, web_search, http=http)
    assert tools.invoke("web_search", {"query": "q"}).text == "[]"
    assert len(http.requests) == 2 and rt.trace.events[0]["tries"] == 2

    http = ScriptedHttp(response(401, {"error": "bad key"}))
    tools, _ = toolset(tmp_path, web_search, http=http)
    with pytest.raises(ToolFailure, match="Brave Search returned HTTP 401"):
        tools.invoke("web_search", {"query": "q"})
    assert len(http.requests) == 1


def test_workspace_write_then_read(tmp_path):
    writes, _ = toolset(tmp_path, workspace_write)
    reads, _ = toolset(tmp_path, workspace_read)
    assert writes.invoke("workspace_write", {"path": "notes/a.txt", "content": "héllo"}) == ToolResult("notes/a.txt")
    assert (tmp_path / "notes" / "a.txt").read_text(encoding="utf-8") == "héllo"
    assert reads.invoke("workspace_read", {"path": "notes/a.txt"}) == ToolResult("héllo")
    assert reads.invoke("workspace_read", {"path": str(tmp_path / "notes" / "a.txt")}) == ToolResult("héllo")


@pytest.mark.parametrize("path", ["../outside.txt", "notes/../../outside.txt", "/etc/hosts"])
def test_workspace_paths_cannot_escape(tmp_path, path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for fn in (workspace_read, workspace_write):
        tools, _ = toolset(workspace, fn)
        result = tools.invoke(fn.__name__, {"path": path, "content": "x"} if fn is workspace_write else {"path": path})
        assert result == ToolResult(f"path escapes the workspace: {path}", is_error=True)
    assert not (tmp_path / "outside.txt").exists()


def test_workspace_read_errors_and_truncation(tmp_path):
    tools, _ = toolset(tmp_path, workspace_read)
    (tmp_path / "blob.bin").write_bytes(b"\x89PNG\x00\x01")
    (tmp_path / "latin1.txt").write_bytes("caf\xe9 au lait".encode("latin-1"))
    (tmp_path / "big.txt").write_bytes(b"a" * 999_999 + "é".encode() + b"tail")      # é straddles the 1 MB limit
    (tmp_path / "dir").mkdir()
    assert tools.invoke("workspace_read", {"path": "missing.txt"}) == ToolResult("no such file: missing.txt", True)
    assert tools.invoke("workspace_read", {"path": "dir"}) == ToolResult("no such file: dir", True)
    assert tools.invoke("workspace_read", {"path": "blob.bin"}).text == "blob.bin is a binary file, not UTF-8 text"
    assert tools.invoke("workspace_read", {"path": "latin1.txt"}).text == "latin1.txt is not UTF-8 text"

    @tool
    def probe(path: str) -> list:
        """Call the builtin directly inside a tool run (the model-facing result is capped at 100 000 chars)."""
        text = workspace_read(path)
        return [len(text), text[-30:]]

    probes, _ = toolset(tmp_path, probe)
    assert json.loads(probes.invoke("probe", {"path": "big.txt"}).text) == [
        999_999 + len("\n…[truncated 5 bytes]"), "a" * 9 + "\n…[truncated 5 bytes]"]      # the cut é is dropped


def test_shell_runs_allowlisted_executables_without_a_shell(tmp_path):
    tools, _ = toolset(tmp_path, shell.allow("echo", "ls"))
    result = json.loads(tools.invoke("shell", {"command": ["echo", "$HOME && ls *"]}).text)
    assert result == {"exit_code": 0, "stdout": "$HOME && ls *\n", "stderr": ""}
    failed = json.loads(tools.invoke("shell", {"command": ["ls", "definitely-missing-file"]}).text)
    assert failed["exit_code"] != 0 and failed["stderr"]


def test_shell_refuses_executables_outside_the_allowlist(tmp_path):
    tools, _ = toolset(tmp_path, shell.allow("echo"))
    assert tools.invoke("shell", {"command": ["sh", "-c", "echo pwned"]}) == ToolResult(
        "'sh' is not allowed; this step's shell tool may run: echo", is_error=True)
    assert tools.invoke("shell", {"command": ["/usr/bin/python3", "-c", "1"]}).text.startswith("'python3' is not")
    assert tools.invoke("shell", {"command": []}).text == "command must be a non-empty argv list"
    script = tmp_path / "echo"
    script.write_text("#!/bin/sh\necho pwned\n")
    script.chmod(0o755)
    assert tools.invoke("shell", {"command": ["./echo", "hi"]}) == ToolResult(
        "the shell tool runs installed executables, not files in the workspace", is_error=True)
    with pytest.raises(ToolInputError, match="may run: nothing"):
        shell(["echo", "bare shell never runs"])


def test_shell_timeout_truncation_and_missing_command(tmp_path):
    tools, _ = toolset(tmp_path, shell.allow("sleep", "seq", "no-such-command-wynd"))
    assert tools.invoke("shell", {"command": ["sleep", "5"], "timeout_s": 1}) == ToolResult("timed out after 1s", True)
    out = json.loads(tools.invoke("shell", {"command": ["seq", "1", "20000"]}).text)["stdout"]
    assert out.startswith("1\n2\n") and len(out) == 20_000 + len("…[truncated 88894 chars]")
    assert out.endswith("…[truncated 88894 chars]")
    assert tools.invoke("shell", {"command": ["no-such-command-wynd"]}) == ToolResult(
        "command not found: no-such-command-wynd", is_error=True)
    assert tools.invoke("shell", {"command": ["seq", "1"], "timeout_s": 601}).text.startswith("invalid arguments")


def test_now_is_a_normalisable_utc_timestamp(tmp_path):
    tools, _ = toolset(tmp_path, now)
    text = tools.invoke("now", {}).text
    assert DATETIME_RE.fullmatch(text) and text.endswith("+00:00")
    assert abs((datetime.fromisoformat(text) - datetime.now(UTC)).total_seconds()) < 5

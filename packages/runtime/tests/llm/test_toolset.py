"""`ToolSet`: invocation outcomes, retry policy, cassette replay/record of network and MCP calls, `tool.call` events,
harness tracing, handles, `for_step`, `handles_for` and `current_runtime` (PLAN §3.9, §3.16, §5.5; `$DRAFTS/03 §9.2`).

The runtime handle and the cassette session are doubles (RT-STEP and RT-CASSETTE build the real ones in parallel).
"""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from wynd.runtime.agentic.errors import McpConfigError, MissingEnvVar, ToolFailure, ToolInputError
from wynd.runtime.cassettes import CassetteMissError
from wynd.runtime.errors import StepFailure
from wynd.runtime.http import HttpError
from wynd.runtime.mcp import McpServer, snapshot
from wynd.runtime.mcp.client import McpError
from wynd.runtime.mcp.entry import McpServerEntry
from wynd.runtime.mcp.fake_server import serve_http
from wynd.runtime.mcp.snapshot import McpToolSpec
from wynd.runtime.providers.types import McpServerRef, ToolResult, ToolSchema
from wynd.runtime.tools import current_runtime, handles_for, is_transient, now, tool, workspace_write
from wynd.runtime.tools.decorator import tool_spec
from wynd.runtime.tools.toolset import ToolSet
from wynd.spec.lockfiles import RetryPolicy


class Trace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, type: str, **fields: Any) -> None:
        self.events.append({"type": type, **fields})


def runtime(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(run_id="run_1", step_path="extract", workspace=tmp_path, trace=Trace(), http=None)


class Cassettes:
    """The CassetteSession surface ToolSet uses (`mode`, `find_tool`, `write_tool`); entries expose `.result` and
    `.error`."""

    def __init__(self, mode: str, recorded: dict[tuple[str, str], ToolResult | str] | None = None) -> None:
        self.mode = mode
        self.recorded = dict(recorded or {})
        self.written: list[tuple[str, dict, ToolResult | None, str | None]] = []

    def find_tool(self, name: str, arguments: dict) -> SimpleNamespace:
        found = self.recorded.get((name, json.dumps(arguments, sort_keys=True)))
        if found is None:
            raise CassetteMissError(key="k" * 64, dir="/cassettes")
        if isinstance(found, str):
            return SimpleNamespace(result=None, error=found)
        return SimpleNamespace(result=found, error=None)

    def write_tool(self, name: str, arguments: dict, result: ToolResult | None, error: str | None = None) -> None:
        self.written.append((name, arguments, result, error))


def flaky(errors: list[BaseException], *, idempotent: bool, effects: tuple[str, ...] = ("network",)):
    """A tool that raises each of `errors` in turn, then returns "ok"."""
    calls = []

    @tool(effects=effects, idempotent=idempotent)
    def fetch(url: str) -> str:
        """Fetch something."""
        calls.append(url)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return "ok"

    return fetch, calls


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("wynd.runtime.tools.toolset.time.sleep", slept.append)
    return slept


def test_idempotent_transient_failure_is_retried_exactly_once(tmp_path, sleeps):
    fetch, calls = flaky([ConnectionError("reset")], idempotent=True)
    rt = runtime(tmp_path)
    tools = ToolSet([tool_spec(fetch, "builtin")], runtime=rt, tool_retries=1)
    assert tools.invoke("fetch", {"url": "u"}) == ToolResult("ok")
    assert calls == ["u", "u"] and sleeps == [0.5] and tools.calls == 1
    [event] = rt.trace.events
    assert event["tries"] == 2 and event["ok"] is True

    fetch, calls = flaky([ConnectionError("reset"), TimeoutError("slow")], idempotent=True)
    tools = ToolSet([tool_spec(fetch, "builtin")], runtime=runtime(tmp_path), tool_retries=1)
    with pytest.raises(ToolFailure, match="tool fetch failed: TimeoutError: slow"):
        tools.invoke("fetch", {"url": "u"})
    assert len(calls) == 2


def test_non_idempotent_tools_are_never_retried(tmp_path, sleeps):
    fetch, calls = flaky([ConnectionError("reset")], idempotent=False)
    tools = ToolSet([tool_spec(fetch, "builtin")], runtime=runtime(tmp_path), tool_retries=3)
    with pytest.raises(ToolFailure, match="ConnectionError: reset"):
        tools.invoke("fetch", {"url": "u"})
    assert calls == ["u"] and sleeps == []


def test_non_transient_errors_are_not_retried(tmp_path, sleeps):
    fetch, calls = flaky([ValueError("bad data")], idempotent=True)
    rt = runtime(tmp_path)
    tools = ToolSet([tool_spec(fetch, "builtin")], runtime=rt, tool_retries=1)
    with pytest.raises(ToolFailure) as info:
        tools.invoke("fetch", {"url": "u"})
    assert calls == ["u"] and sleeps == []
    assert isinstance(info.value.__cause__, ValueError)
    [event] = rt.trace.events
    assert event["ok"] is False and event["error"] == "tool fetch failed: ValueError: bad data"
    assert event["result"] is None and event["tries"] == 1


@pytest.mark.parametrize(
    ("exc", "transient"),
    [
        (ConnectionError("reset"), True),
        (TimeoutError("slow"), True),
        (urllib.error.URLError("dns"), True),
        (urllib.error.HTTPError("u", 503, "busy", {}, None), False),
        (HttpError("no connection", response=None), True),
        (HttpError("busy", response=SimpleNamespace(status=503)), True),
        (HttpError("throttled", response=SimpleNamespace(status=429)), True),
        (HttpError("missing", response=SimpleNamespace(status=404)), False),
        (McpError("down", transient=True), True),
        (McpError("bad request"), False),
        (ValueError("x"), False),
    ],
)
def test_is_transient(exc, transient):
    assert is_transient(exc) is transient


def test_tool_input_error_and_bad_arguments_go_back_to_the_model(tmp_path):
    @tool
    def pick(index: int) -> str:
        """Pick an item."""
        if index > 2:
            raise ToolInputError(f"index {index} is out of range (0..2)")
        return "abc"[index]

    rt = runtime(tmp_path)
    tools = ToolSet([tool_spec(pick, "builtin")], runtime=rt)
    assert tools.invoke("pick", {"index": 5}) == ToolResult("index 5 is out of range (0..2)", is_error=True)
    invalid = tools.invoke("pick", {"index": "x", "extra": 1})
    assert invalid.is_error and invalid.text.startswith("invalid arguments:\n- index: ")
    assert "\n- extra: Extra inputs are not permitted" in invalid.text
    assert tools.invoke("nope", {}) == ToolResult("unknown tool 'nope'", is_error=True)
    assert tools.calls == 2                                     # unknown tools are not counted
    assert [(e["ok"], e["is_error"], e["tries"]) for e in rt.trace.events] == [(True, True, 1), (True, True, 0)]


@pytest.mark.parametrize("exc", [
    CassetteMissError(key="k", dir="/c"),
    MissingEnvVar("CRM_TOKEN"),
    McpConfigError("server gone"),
    StepFailure("config", "step does not declare effects: [network]"),
])
def test_step_level_errors_propagate_unchanged(tmp_path, exc):
    @tool(idempotent=True)
    def boom() -> str:
        """Raise."""
        raise exc

    tools = ToolSet([tool_spec(boom, "builtin")], runtime=runtime(tmp_path), tool_retries=1)
    with pytest.raises(type(exc)) as info:
        tools.invoke("boom", {})
    assert info.value is exc


def test_tool_call_event_fields(tmp_path):
    @tool(effects=["network"], idempotent=True)
    def echo(text: str) -> str:
        """Echo."""
        return text.upper()

    rt = runtime(tmp_path)
    ToolSet([tool_spec(echo, "builtin")], runtime=rt).invoke("echo", {"text": "hi"})
    [event] = rt.trace.events
    assert event.pop("latency_ms") >= 0
    assert event == {"type": "tool.call", "tool": "echo", "source": "builtin", "effects": ["network"],
                     "idempotent": True, "args": {"text": "hi"}, "result": "HI", "ok": True, "is_error": False,
                     "error": None, "tries": 1, "replayed": False}


def test_trace_args_and_results_are_truncated_to_4kb(tmp_path):
    @tool
    def echo(text: str) -> str:
        """Echo."""
        return text

    rt = runtime(tmp_path)
    ToolSet([tool_spec(echo, "builtin")], runtime=rt).invoke("echo", {"text": "y" * 5000})
    [event] = rt.trace.events
    assert isinstance(event["args"], str) and event["args"].startswith('{"text": "yyy')
    assert len(event["result"]) < 4200 and event["result"].endswith("…[truncated 904 chars]")


def test_current_runtime_is_bound_while_a_tool_runs(tmp_path):
    @tool
    def whoami() -> str:
        """The run id."""
        return current_runtime().run_id

    assert ToolSet([tool_spec(whoami, "builtin")], runtime=runtime(tmp_path)).invoke("whoami", {}).text == "run_1"
    with pytest.raises(RuntimeError, match="only available while a tool runs inside a step run"):
        current_runtime()
    [handle] = handles_for([whoami])                          # outside a step run there is no handle
    with pytest.raises(ToolFailure, match="RuntimeError"):
        handle.invoke({})


def test_replay_serves_network_tools_from_the_cassette(tmp_path):
    fetch, calls = flaky([], idempotent=True)
    rt = runtime(tmp_path)
    cassettes = Cassettes("replay", {
        ("fetch", '{"url": "a"}'): ToolResult("recorded a"),
        ("fetch", '{"url": "b"}'): "tool fetch failed: ConnectionError: reset",
    })
    tools = ToolSet([tool_spec(fetch, "builtin")], runtime=rt, cassettes=cassettes, tool_retries=1)
    assert tools.invoke("fetch", {"url": "a"}) == ToolResult("recorded a")
    with pytest.raises(ToolFailure, match="tool fetch failed: ConnectionError: reset"):
        tools.invoke("fetch", {"url": "b"})
    with pytest.raises(CassetteMissError, match="no recording for this request"):
        tools.invoke("fetch", {"url": "c"})
    assert calls == [] and tools.calls == 3
    assert [(e["replayed"], e["ok"]) for e in rt.trace.events] == [(True, True), (True, False)]


def test_replay_executes_local_tools(tmp_path):
    rt = runtime(tmp_path)
    tools = ToolSet([tool_spec(workspace_write, "builtin")], runtime=rt, cassettes=Cassettes("replay"))
    assert tools.invoke("workspace_write", {"path": "out/a.txt", "content": "hi"}).text == "out/a.txt"
    assert (tmp_path / "out/a.txt").read_text() == "hi"
    assert rt.trace.events[0]["replayed"] is False


def test_record_writes_network_calls_only(tmp_path):
    fetch, _ = flaky([], idempotent=True)
    broken, _ = flaky([ValueError("nope")], idempotent=False)
    cassettes = Cassettes("record")
    specs = [tool_spec(fetch, "builtin"), tool_spec(workspace_write, "builtin"),
             tool_spec(tool(name="broken", effects=["network"])(broken), "builtin")]
    tools = ToolSet(specs, runtime=runtime(tmp_path), cassettes=cassettes)
    tools.invoke("fetch", {"url": "a"})
    tools.invoke("workspace_write", {"path": "x.txt", "content": "x"})
    with pytest.raises(ToolFailure):
        tools.invoke("broken", {"url": "b"})
    assert cassettes.written == [
        ("fetch", {"url": "a"}, ToolResult("ok"), None),
        ("broken", {"url": "b"}, None, "tool broken failed: ValueError: nope"),
    ]


def test_handles_mark_local_tools(tmp_path):
    fetch, _ = flaky([], idempotent=True)
    tools = ToolSet([tool_spec(fetch, "builtin"), tool_spec(workspace_write, "builtin"), tool_spec(now, "builtin")],
                    runtime=runtime(tmp_path), mcp=[mcp_block("http://127.0.0.1:9/mcp")])
    handles = {h.name: h for h in tools.handles()}
    assert {name: h.local for name, h in handles.items()} == {
        "fetch": False, "workspace_write": True, "now": True, "github__get_issue": False, "github__list_issues": False,
    }
    assert handles["fetch"].invoke({"url": "u"}) == ToolResult("ok")
    assert tools.calls == 1


def test_duplicate_tool_names_are_refused():
    fetch, _ = flaky([], idempotent=True)
    with pytest.raises(ValueError, match="duplicate tool name 'fetch'"):
        ToolSet([tool_spec(fetch, "builtin"), tool_spec(fetch, "builtin")])


def test_trace_harness_calls(tmp_path):
    fetch, _ = flaky([], idempotent=True)
    transcript = [
        {"type": "text", "text": "Let me look."},
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "a.txt"}},
        {"type": "tool_result", "id": "t1", "is_error": False, "content": "hello"},
        {"type": "tool_use", "id": "t2", "name": "fetch", "input": {"url": "u"}},
        {"type": "tool_result", "id": "t2", "is_error": False, "content": "ok"},
        {"type": "tool_use", "id": "t3", "name": "workspace_write", "input": {"path": "p", "content": "c"}},
        {"type": "tool_use", "id": "t4", "name": "StructuredOutput", "input": {"output": {}}},
    ]
    specs = [tool_spec(fetch, "builtin"), tool_spec(workspace_write, "builtin")]

    live = runtime(tmp_path)
    ToolSet(specs, runtime=live).trace_harness_calls(transcript)
    assert live.trace.events == [
        {"type": "tool.call", "tool": "Read", "source": "harness", "effects": [], "idempotent": False,
         "args": {"file_path": "a.txt"}, "result": "hello", "ok": True, "is_error": False, "error": None, "tries": 1,
         "latency_ms": None, "replayed": False},
    ]

    replay = runtime(tmp_path)
    ToolSet(specs, runtime=replay, cassettes=Cassettes("replay")).trace_harness_calls(transcript)
    assert [(e["tool"], e["source"], e["result"], e["replayed"]) for e in replay.trace.events] == [
        ("Read", "harness", "hello", True),
        ("fetch", "builtin", "ok", True),          # recorded network call, not re-executed on replay
    ]


class Triage:
    tools = [workspace_write]

    def __init__(self) -> None:
        self.prefix = "issue"

    @tool
    def label(self, number: int) -> str:
        """Label an issue."""
        return f"{self.prefix}-{number}"


def mcp_block(url: str, *, description: str = "Get a GitHub issue by number.") -> dict[str, Any]:
    tools = [
        McpToolSpec("get_issue", description, {"type": "object", "properties": {"number": {"type": "integer"}},
                                               "required": ["number"]}, {"readOnlyHint": True}),
        McpToolSpec("list_issues", "List the issues of the repository.",
                    {"type": "object", "properties": {"state": {"type": "string"}}}, {"idempotentHint": True}),
    ]
    snap = snapshot(McpServer("github", allow=["get_issue", "list_issues"]), tools)
    entry = McpServerEntry(name="github", transport="http", url=url,
                           headers={"Authorization": "Bearer ${env:GITHUB_TOKEN}"}, auth_env=["GITHUB_TOKEN"])
    return {**snap.model_dump(mode="json"), "entry": entry.model_dump(mode="json")}


def agent_call(tmp_path: Path, mcp: list[dict[str, Any]]) -> SimpleNamespace:
    policy = SimpleNamespace(retries=RetryPolicy(run=2, validation=2, tool=1), mcp=mcp)
    return SimpleNamespace(step_path="triage", policy=policy, runtime=runtime(tmp_path))


def test_for_step_binds_methods_and_adds_mcp_tools(tmp_path):
    step = Triage()
    step.prefix = "bug"
    tools = ToolSet.for_step(step, agent_call(tmp_path, [mcp_block("http://127.0.0.1:9/mcp")]), None)
    assert tools.invoke("label", {"number": 3}).text == "bug-3"
    assert [s.name for s in tools.schemas()] == ["label", "workspace_write", "github__get_issue",
                                                 "github__list_issues"]
    assert tools.schemas()[2] == ToolSchema(
        "github__get_issue", "Get a GitHub issue by number.",
        {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]},
    )
    assert tools.mcp_refs() == [McpServerRef("github", ("get_issue", "list_issues"))]


def test_mcp_tools_are_proxied_to_the_live_server(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    with serve_http(token="gh-secret") as server:
        call = agent_call(tmp_path, [mcp_block(server.url)])
        cassettes = Cassettes("record")
        tools = ToolSet.for_step(Triage(), call, cassettes)
        tools.connect_mcp()
        try:
            assert json.loads(tools.invoke("github__get_issue", {"number": 42}).text) == {
                "number": 42, "state": "open", "title": "Login page broken"}
            assert tools.invoke("github__list_issues", {}).text == '{"issues": [{"number": 42, "state": "open"}]}'
        finally:
            tools.close()
        calls = [body for method, _, body in server.requests if method == "POST" and body["method"] == "tools/call"]
        assert [c["params"] for c in calls] == [{"name": "get_issue", "arguments": {"number": 42}},
                                                {"name": "list_issues", "arguments": {}}]
        assert [w[0] for w in cassettes.written] == ["github__get_issue", "github__list_issues"]
        [first, _] = call.runtime.trace.events
        assert (first["source"], first["effects"], first["idempotent"]) == ("mcp:github", ["network"], True)
        assert any(method == "DELETE" for method, _, _ in server.requests)      # close() ended the session


def test_mcp_is_error_results_go_back_to_the_model(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    tools_listed = [McpToolSpec("delete_repo", "Delete a repository (must never be allowed).",
                                {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                                {})]
    snap = snapshot(McpServer("github", allow=["delete_repo"]), tools_listed)
    with serve_http(token="gh-secret") as server:
        entry = McpServerEntry(name="github", transport="http", url=server.url,
                               headers={"Authorization": "Bearer ${env:GITHUB_TOKEN}"})
        tools = ToolSet([], runtime=runtime(tmp_path),
                        mcp=[{**snap.model_dump(mode="json"), "entry": entry.model_dump(mode="json")}])
        tools.connect_mcp()
        try:
            assert tools.invoke("github__delete_repo", {"name": "wynd"}) == ToolResult("forbidden", is_error=True)
        finally:
            tools.close()


def test_replay_never_connects_to_mcp_servers(tmp_path):
    cassettes = Cassettes("replay", {("github__get_issue", '{"number": 1}'): ToolResult('{"number": 1}')})
    tools = ToolSet.for_step(Triage(), agent_call(tmp_path, [mcp_block("http://127.0.0.1:9/mcp")]), cassettes)
    assert tools.invoke("github__get_issue", {"number": 1}) == ToolResult('{"number": 1}')
    with pytest.raises(CassetteMissError):
        tools.invoke("github__list_issues", {"state": "closed"})


def test_an_unconnected_mcp_server_is_a_config_error(tmp_path):
    tools = ToolSet([], runtime=runtime(tmp_path), mcp=[mcp_block("http://127.0.0.1:9/mcp")])
    with pytest.raises(McpConfigError, match="is not connected"):
        tools.invoke("github__get_issue", {"number": 1})


def test_handles_for_serves_non_step_callers():
    @tool
    def read_design(process: str) -> dict:
        """Read a process design."""
        if process == "missing":
            raise KeyError(process)
        return {"process": process, "yaml": "kind: process"}

    [handle] = handles_for([read_design])
    assert handle.local is True and handle.input_schema["required"] == ["process"]
    assert json.loads(handle.invoke({"process": "p"}).text) == {"process": "p", "yaml": "kind: process"}
    assert handle.invoke({}).is_error
    with pytest.raises(ToolFailure, match="tool read_design failed: KeyError: 'missing'"):
        handle.invoke({"process": "missing"})

"""The `claude-code` AgentProvider with real claude-agent-sdk message types and an injected `query_fn` (no CLI is
spawned): options, the in-process tool bridge, transcript and usage mapping, stateless continuation, error
classification, cancellation and session cleanup (PLAN §3.15, §1.4; `$DRAFTS/03 §7`, §16).

`render_prompt` and the output-schema envelope belong to RT-LOOP; here they are doubles, so these tests pin what the
provider does with them.
"""

import json
import logging
import sys
import threading
from pathlib import Path

import claude_agent_sdk as sdk
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
    RateLimitEvent,
    RateLimitInfo,
    ResultError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from wynd.runtime.agentic.errors import ToolFailure
from wynd.runtime.providers import claude_code
from wynd.runtime.providers.claude_code import AUTH_HINT, ClaudeCodeProvider
from wynd.runtime.providers.types import AgentRequest, Continuation, ProviderError, ToolHandle, ToolResult
from wynd.runtime.usage import Usage

SID = "8f14e45f-ceea-467a-9575-6f0b1a5c2d3e"
MODEL = "claude-haiku-4-5-20251001"
SCHEMA = {"type": "object", "properties": {"exit": {"const": "done"}, "total": {"type": "number"}}}
TIERS = {"cheap": "haiku", "standard": "sonnet", "strong": "opus"}
KEY = {"project_key": "k", "session_id": SID}


def render_double(instruction, context, input, *, raw_prompt=None):
    if raw_prompt is not None:
        return instruction, raw_prompt
    return f"SYSTEM:{instruction}", "USER:" + json.dumps({"context": context, "input": input}, sort_keys=True)


def wrap_double(schema: dict) -> dict:
    return {"type": "object", "properties": {"output": schema}, "required": ["output"], "additionalProperties": False}


def unwrap_double(value):
    return value["output"] if isinstance(value, dict) and set(value) == {"output"} else value


@pytest.fixture(autouse=True)
def doubles(monkeypatch):
    monkeypatch.setattr(claude_code, "render_prompt", render_double)
    monkeypatch.setattr(claude_code, "wrap_output_schema", wrap_double)
    monkeypatch.setattr(claude_code, "unwrap_output", unwrap_double)


@pytest.fixture(autouse=True)
def config_dir(monkeypatch, tmp_path: Path) -> Path:
    """Session cleanup must never touch the real ~/.claude."""
    path = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(path))
    return path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "ws"
    path.mkdir()
    return path


@pytest.fixture
def servers(monkeypatch) -> list[tuple[str, list]]:
    """Records the SDK tools handed to `create_sdk_mcp_server` so a fake stream can call their handlers."""
    created = []
    real = sdk.create_sdk_mcp_server

    def record(name, version="1.0.0", tools=None):
        created.append((name, list(tools or [])))
        return real(name, version=version, tools=tools)

    monkeypatch.setattr(sdk, "create_sdk_mcp_server", record)
    return created


class FakeQuery:
    """Stands in for `claude_agent_sdk.query`: records the prompt and options, then yields the scripted messages.
    An exception item is raised; a callable item is awaited with the query (drives tools/stores mid-stream)."""

    def __init__(self, *items):
        self.items = items
        self.prompt = None
        self.options = None
        self.closed = False

    async def __call__(self, *, prompt, options):
        self.prompt, self.options = prompt, options
        try:
            for item in self.items:
                if isinstance(item, BaseException):
                    raise item
                if callable(item):
                    await item(self)
                    continue
                yield item
        finally:
            self.closed = True


def init(model: str = MODEL) -> SystemMessage:
    return SystemMessage(subtype="init", data={"session_id": SID, "model": model, "tools": ["StructuredOutput"]})


def assistant(*blocks) -> AssistantMessage:
    return AssistantMessage(content=list(blocks), model=MODEL)


def model_usage(inp=2000, out=100, cr=50, cw=10, cost=0.002) -> dict:
    return {MODEL: {"inputTokens": inp, "outputTokens": out, "cacheReadInputTokens": cr,
                    "cacheCreationInputTokens": cw, "costUSD": cost, "costBasis": "list"}}


def result(structured=None, *, text=None, cost=0.002, usage=None, is_error=False, subtype="success",
           status=None, stop_reason="end_turn") -> ResultMessage:
    return ResultMessage(subtype=subtype, duration_ms=1200, duration_api_ms=1000, is_error=is_error, num_turns=2,
                         session_id=SID, stop_reason=stop_reason, total_cost_usd=cost,
                         usage={"input_tokens": 12, "output_tokens": 34}, result=text, structured_output=structured,
                         model_usage=model_usage(cost=cost) if usage is None else usage, api_error_status=status)


def done(total: float = 12.5) -> ResultMessage:
    return result({"output": {"exit": "done", "total": total}}, text='{"output": ...}')


def request(workspace: Path, **fields) -> AgentRequest:
    base = dict(model_id="haiku", thinking="low", instruction="Extract the invoice fields.",
                context={"process.goal": "Pay invoices"}, input={"invoice_text": "INV-1"}, output_schema=SCHEMA,
                tools=[], mcp_servers=[], workspace=workspace)
    return AgentRequest(**{**base, **fields})


def handle(invoke, name: str = "lookup_rate") -> ToolHandle:
    return ToolHandle(name=name, description="Look up an exchange rate.",
                      input_schema={"type": "object", "properties": {"currency": {"type": "string"}},
                                    "required": ["currency"]},
                      invoke=invoke)


def call_tool(servers, name: str, args: dict, results: list):
    async def step(query):
        _, tools = servers[-1]
        tool = next(t for t in tools if t.name == name)
        results.append(await tool.handler(args))
    return step


def run(query: FakeQuery, req: AgentRequest):
    return ClaudeCodeProvider(tiers=TIERS, query_fn=query).run(req)


# --- options ----------------------------------------------------------------------------------------------------------


def test_options_isolate_the_harness(workspace: Path):
    q = FakeQuery(init(), done())
    run(q, request(workspace, max_turns=7))
    o = q.options
    assert o.model == "haiku"
    assert o.system_prompt == "SYSTEM:Extract the invoice fields."
    assert q.prompt == 'USER:{"context": {"process.goal": "Pay invoices"}, "input": {"invoice_text": "INV-1"}}'
    assert o.tools == [] and o.allowed_tools == [] and o.mcp_servers == {}
    assert o.strict_mcp_config is True
    assert o.setting_sources == []
    assert o.permission_mode == "dontAsk"
    assert o.output_format == {"type": "json_schema", "schema": wrap_double(SCHEMA)}
    assert o.cwd == str(workspace)
    assert o.max_turns == 7
    assert o.effort == "low"
    assert o.resume is None
    assert o.session_store is not None
    assert o.env == {"CLAUDE_AGENT_SDK_CLIENT_APP": "wynd/0.1.0", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}


@pytest.mark.parametrize(("thinking", "effort"), [("none", "low"), ("low", "low"), ("medium", "medium"),
                                                  ("high", "high")])
def test_effort_follows_thinking(workspace: Path, thinking: str, effort: str):
    q = FakeQuery(init(), done())
    run(q, request(workspace, thinking=thinking))
    assert q.options.effort == effort


def test_tools_are_bridged_over_the_in_process_server(workspace: Path, servers):
    q = FakeQuery(init(), done())
    h = handle(lambda args: ToolResult("1.25"))
    run(q, request(workspace, tools=[h, handle(lambda args: ToolResult("x"), name="github__get_issue")]))
    o = q.options
    assert set(o.mcp_servers) == {"wynd"}
    assert o.mcp_servers["wynd"]["type"] == "sdk"
    assert o.allowed_tools == ["mcp__wynd__lookup_rate", "mcp__wynd__github__get_issue"]
    assert o.tools == []
    name, tools = servers[-1]
    assert name == "wynd"
    assert [(t.name, t.description, t.input_schema) for t in tools][0] == ("lookup_rate", h.description,
                                                                          h.input_schema)


def test_builtin_tools_use_the_claude_code_preset(workspace: Path):
    q = FakeQuery(init(), done())
    run(q, request(workspace, builtin_tools=["Read", "Grep"], tools=[handle(lambda a: ToolResult(""))]))
    o = q.options
    assert o.system_prompt == {"type": "preset", "preset": "claude_code",
                               "append": "SYSTEM:Extract the invoice fields."}
    assert o.tools == ["Read", "Grep"]
    assert o.allowed_tools == ["mcp__wynd__lookup_rate", "Read", "Grep"]


def test_raw_mode_free_text(workspace: Path):
    q = FakeQuery(init(), assistant(TextBlock("Hi there.")), result(None, text="Hi there."))
    resp = run(q, request(workspace, instruction="You are the Wynd chat.", prompt="Hello", output_schema=None))
    assert q.options.output_format is None
    assert q.options.system_prompt == "You are the Wynd chat."
    assert q.prompt == "Hello"
    assert resp.structured_output is None
    assert resp.note == "Hi there."


def test_note_falls_back_to_the_result_text_without_structured_output(workspace: Path):
    q = FakeQuery(init(), assistant(TextBlock("Thinking out loud.")), result(None, text="I cannot do that " * 50))
    resp = run(q, request(workspace))
    assert resp.structured_output is None
    assert resp.note == ("I cannot do that " * 50)[:500]


# --- response mapping -------------------------------------------------------------------------------------------------


def test_response_mapping(workspace: Path, servers):
    events, tool_results, invoked = [], [], []
    h = handle(lambda args: invoked.append(args) or ToolResult("1.25"))
    q = FakeQuery(
        init(),
        assistant(TextBlock("Looking up."),
                  ToolUseBlock(id="t1", name="mcp__wynd__lookup_rate", input={"currency": "USD"})),
        call_tool(servers, "lookup_rate", {"currency": "USD"}, tool_results),
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content=[{"type": "text", "text": "1.25"}],
                                             is_error=False)]),
        assistant(ToolUseBlock(id="t2", name="Read", input={"file_path": "notes.txt"})),
        UserMessage(content=[ToolResultBlock(tool_use_id="t2", content="x" * 5000, is_error=None)]),
        assistant(TextBlock("  "), TextBlock("Done."),
                  ToolUseBlock(id="t3", name="StructuredOutput", input={"output": {"exit": "done"}})),
        done(),
    )
    resp = run(q, request(workspace, tools=[h], builtin_tools=["Read"], on_event=events.append))
    assert resp.transcript == [
        {"type": "text", "text": "Looking up."},
        {"type": "tool_use", "id": "t1", "name": "lookup_rate", "input": {"currency": "USD"}},
        {"type": "tool_result", "id": "t1", "is_error": False, "content": "1.25"},
        {"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "notes.txt"}},
        {"type": "tool_result", "id": "t2", "is_error": False, "content": "x" * 4000},
        {"type": "text", "text": "Done."},
    ]
    assert events == resp.transcript
    assert invoked == [{"currency": "USD"}]
    assert tool_results == [{"content": [{"type": "text", "text": "1.25"}], "is_error": False}]
    assert resp.structured_output == {"exit": "done", "total": 12.5}
    assert resp.note == "Done."
    assert resp.model_id == MODEL                      # reported by the init message, not the alias
    assert resp.tool_calls == 2                        # one bridged call + one harness built-in
    assert resp.startup_ms is not None and resp.startup_ms >= 0
    assert resp.cost_basis == "list"
    assert resp.usage.model_dump(exclude={"latency_ms"}) == Usage(
        input_tokens=2000, output_tokens=100, cache_read_tokens=50, cache_write_tokens=10, cost_usd=0.002, calls=1,
    ).model_dump(exclude={"latency_ms"})
    assert resp.usage.latency_ms > 0
    assert resp.session == {"session_id": SID, "entries": [],
                            "cum": {"in": 2000, "out": 100, "cr": 50, "cw": 10, "cost": 0.002}}


def test_usage_sums_every_model_and_handles_unknown_cost(workspace: Path):
    usage = {**model_usage(), "claude-sonnet-5": {"inputTokens": 5, "outputTokens": 1}}
    q = FakeQuery(init(), result({"output": {}}, cost=None, usage=usage))
    resp = run(q, request(workspace))
    assert (resp.usage.input_tokens, resp.usage.output_tokens, resp.usage.cost_usd) == (2005, 101, None)


def test_bridge_invokes_the_handle_in_a_worker_thread(workspace: Path, servers):
    seen, results = [], []

    def invoke(args):
        seen.append((args, threading.current_thread() is threading.main_thread()))
        return ToolResult("no such currency", is_error=True)

    q = FakeQuery(init(), call_tool(servers, "lookup_rate", {"currency": "XXX"}, results), done())
    resp = run(q, request(workspace, tools=[handle(invoke)]))
    assert seen == [({"currency": "XXX"}, False)]
    assert results == [{"content": [{"type": "text", "text": "no such currency"}], "is_error": True}]
    assert resp.tool_calls == 1


def test_a_raising_tool_aborts_the_harness(workspace: Path, servers):
    failure = ToolFailure("tool lookup_rate failed: RuntimeError: down")

    def invoke(args):
        raise failure

    results = []
    q = FakeQuery(init(), call_tool(servers, "lookup_rate", {"currency": "USD"}, results),
                  call_tool(servers, "lookup_rate", {"currency": "EUR"}, results),
                  assistant(TextBlock("I will try something else.")), done())
    with pytest.raises(ToolFailure) as err:
        run(q, request(workspace, tools=[handle(invoke)]))
    assert err.value is failure
    assert results[0]["is_error"] is True
    assert results[0]["content"][0]["text"].startswith("tool failed: tool lookup_rate failed")
    assert results[1] == {"content": [{"type": "text", "text": "step aborted"}], "is_error": True}
    assert q.closed


def test_a_tool_failure_wins_over_the_harness_dying_after_it(workspace: Path, servers):
    failure = ToolFailure("tool lookup_rate failed: OSError: disk full")

    def invoke(args):
        raise failure

    q = FakeQuery(init(), call_tool(servers, "lookup_rate", {"currency": "USD"}, []),
                  ProcessError("CLI exited", exit_code=1))
    with pytest.raises(ToolFailure) as err:
        run(q, request(workspace, tools=[handle(invoke)]))
    assert err.value is failure


def test_a_cassette_miss_in_a_tool_aborts_the_harness(workspace: Path, servers):
    from wynd.runtime.cassettes import CassetteMissError

    miss = CassetteMissError(key="abc123", dir="/c")

    def invoke(args):
        raise miss

    q = FakeQuery(init(), call_tool(servers, "lookup_rate", {"currency": "USD"}, []), assistant(TextBlock("x")),
                  done())
    with pytest.raises(CassetteMissError) as err:
        run(q, request(workspace, tools=[handle(invoke)]))
    assert err.value is miss


# --- continuation -----------------------------------------------------------------------------------------------------


def test_continuation_resumes_the_same_session_statelessly(workspace: Path):
    async def mirror_first(q):
        await q.options.session_store.append(KEY, [{"type": "user", "uuid": "u1"}, {"type": "assistant", "uuid": "a1"}])
        await q.options.session_store.append({**KEY, "subpath": "subagents/x"}, [{"type": "user", "uuid": "s1"}])

    first = FakeQuery(init(), mirror_first, result({"output": {"exit": "done", "total": "bad"}}))
    resp1 = run(first, request(workspace))
    assert resp1.session["entries"] == [{"type": "user", "uuid": "u1"}, {"type": "assistant", "uuid": "a1"}]
    assert first.options.resume is None

    loaded = []

    async def resume_second(q):
        loaded.append(await q.options.session_store.load(KEY))
        loaded.append(await q.options.session_store.load({**KEY, "subpath": "subagents/x"}))
        await q.options.session_store.append(KEY, [{"type": "user", "uuid": "u2"}])

    cumulative = model_usage(inp=3000, out=160, cr=80, cw=10, cost=0.0035)
    second = FakeQuery(init(), resume_second, result({"output": {"exit": "done", "total": 1.0}}, cost=0.0035,
                                                     usage=cumulative))
    resp2 = run(second, request(workspace, continuation=Continuation(session=resp1.session,
                                                                     message="Your output did not validate.")))
    assert second.options.resume == SID
    assert second.prompt == "Your output did not validate."
    assert loaded == [[{"type": "user", "uuid": "u1"}, {"type": "assistant", "uuid": "a1"}], None]
    assert resp2.usage.model_dump(exclude={"latency_ms"}) == Usage(
        input_tokens=1000, output_tokens=60, cache_read_tokens=30, cache_write_tokens=0, cost_usd=0.0015, calls=1,
    ).model_dump(exclude={"latency_ms"})
    assert resp2.session["cum"] == {"in": 3000, "out": 160, "cr": 80, "cw": 10, "cost": 0.0035}
    assert [e["uuid"] for e in resp2.session["entries"]] == ["u1", "a1", "u2"]
    assert resp2.structured_output == {"exit": "done", "total": 1.0}


# --- errors -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "kind", "retryable", "status"),
    [
        ({"subtype": "success", "is_error": True, "api_error_status": 401, "terminal_reason": "api_error",
          "result": "Failed to authenticate. API Error: 401 OAuth access token is invalid."}, "auth", False, 401),
        ({"subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"}, "auth", False, None),
        ({"subtype": "success", "is_error": True, "api_error_status": 400,
          "result": "API Error: 400 tools.0.custom.input_schema.type: Field required"}, "invalid_request", False, 400),
        ({"subtype": "success", "is_error": True, "api_error_status": 529, "result": "API Error: 529 Overloaded"},
         "transport", True, 529),
        ({"subtype": "success", "is_error": True, "terminal_reason": "api_error", "result": "API Error: timeout"},
         "transport", True, None),
        ({"subtype": "error_max_turns", "is_error": True, "errors": ["Reached maximum number of turns (3)"]},
         "max_turns", False, None),
        ({"subtype": "error_during_execution", "is_error": True}, "transport", True, None),
    ],
)
def test_result_errors_are_classified(workspace: Path, data, kind, retryable, status):
    q = FakeQuery(init(), ResultError("Claude Code returned an error result", data=data, exit_code=1))
    with pytest.raises(ProviderError) as err:
        run(q, request(workspace))
    e = err.value
    assert (e.kind, e.retryable, e.status, e.tool_called) == (kind, retryable, status, False)
    if kind == "auth":
        assert str(e).startswith(AUTH_HINT)
    if kind == "max_turns":
        assert "Reached maximum number of turns (3)" in str(e)


def test_error_result_message_without_an_exception_is_classified(workspace: Path):
    q = FakeQuery(init(), result(None, text="Not logged in · Please run /login", is_error=True))
    with pytest.raises(ProviderError) as err:
        run(q, request(workspace))
    assert err.value.kind == "auth"


def test_tool_called_is_reported_on_errors_after_a_tool_ran(workspace: Path):
    q = FakeQuery(init(), assistant(ToolUseBlock(id="t1", name="WebFetch", input={"url": "https://x"})),
                  ResultError("error", data={"subtype": "success", "api_error_status": 529, "result": "Overloaded"}))
    with pytest.raises(ProviderError) as err:
        run(q, request(workspace, builtin_tools=["WebFetch"]))
    assert (err.value.kind, err.value.retryable, err.value.tool_called) == ("transport", True, True)


def test_refusal_is_a_refusal(workspace: Path):
    q = FakeQuery(init(), result(None, text="I can't help with that.", stop_reason="refusal"))
    with pytest.raises(ProviderError) as err:
        run(q, request(workspace))
    assert (err.value.kind, err.value.retryable, str(err.value)) == ("refusal", False, "I can't help with that.")


def test_cli_not_found_is_unavailable(workspace: Path):
    with pytest.raises(ProviderError) as err:
        run(FakeQuery(CLINotFoundError("Claude Code not found")), request(workspace))
    assert (err.value.kind, err.value.retryable) == ("unavailable", False)


@pytest.mark.parametrize("exc", [CLIConnectionError("pipe closed"), ProcessError("CLI died", exit_code=137),
                                 CLIJSONDecodeError("{oops", ValueError("bad"))])
def test_process_failures_are_retryable_transport(workspace: Path, servers, exc):
    q = FakeQuery(init(), call_tool(servers, "lookup_rate", {"currency": "USD"}, []), exc)
    with pytest.raises(ProviderError) as err:
        run(q, request(workspace, tools=[handle(lambda a: ToolResult("1"))]))
    assert (err.value.kind, err.value.retryable, err.value.tool_called) == ("transport", True, True)
    assert str(err.value).startswith("claude-code transport failure:")


def test_a_stream_without_a_result_is_transport(workspace: Path):
    with pytest.raises(ProviderError) as err:
        run(FakeQuery(init(), assistant(TextBlock("..."))), request(workspace))
    assert (err.value.kind, err.value.retryable, str(err.value)) == (
        "transport", True, "claude-code ended without a result")


def test_missing_sdk_is_unavailable(monkeypatch, workspace: Path):
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
    with pytest.raises(ProviderError) as err:
        ClaudeCodeProvider(tiers=TIERS).run(request(workspace))
    assert (err.value.kind, err.value.retryable) == ("unavailable", False)
    assert "needs claude-agent-sdk" in str(err.value)


# --- cancellation, rate limits, cleanup -------------------------------------------------------------------------------


def test_cancel_between_messages_closes_the_harness(workspace: Path):
    cancel = threading.Event()

    async def press_cancel(q):
        cancel.set()

    q = FakeQuery(init(), press_cancel, assistant(TextBlock("still working")), done())
    with pytest.raises(ProviderError) as err:
        run(q, request(workspace, cancel=cancel))
    assert (err.value.kind, err.value.retryable, str(err.value)) == ("transport", False, "cancelled")
    assert q.closed


def test_cancel_before_start_never_starts_the_harness(workspace: Path):
    cancel = threading.Event()
    cancel.set()
    q = FakeQuery(init(), done())
    with pytest.raises(ProviderError):
        run(q, request(workspace, cancel=cancel))
    assert q.options is None


def test_rate_limit_warning_is_logged(workspace: Path, caplog):
    event = RateLimitEvent(rate_limit_info=RateLimitInfo(status="allowed_warning", rate_limit_type="five_hour"),
                           uuid="r1", session_id=SID)
    with caplog.at_level(logging.WARNING, logger="wynd.provider.claude_code"):
        run(FakeQuery(init(), event, done()), request(workspace))
    assert "allowed_warning" in caplog.text and "five_hour" in caplog.text


def session_file(config_dir: Path, workspace: Path) -> Path:
    path = config_dir / "projects" / sdk.project_key_for_directory(str(workspace)) / f"{SID}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"type":"user"}\n')
    return path


def test_the_local_session_is_deleted_after_a_call(workspace: Path, config_dir: Path):
    path = session_file(config_dir, workspace)
    run(FakeQuery(init(), done()), request(workspace))
    assert not path.exists()
    assert not path.parent.exists()
    assert (config_dir / "projects").is_dir()


def test_the_local_session_is_deleted_after_a_failure(workspace: Path, config_dir: Path):
    path = session_file(config_dir, workspace)
    other = path.parent / "other-session.jsonl"
    other.write_text("{}\n")
    q = FakeQuery(init(), ResultError("error", data={"subtype": "success", "api_error_status": 500, "result": "x"}))
    with pytest.raises(ProviderError):
        run(q, request(workspace))
    assert not path.exists()
    assert other.exists()                      # the project dir is removed only when empty


def test_tiers():
    assert ClaudeCodeProvider(tiers={"cheap": "sonnet"}).tiers() == {"cheap": "sonnet"}

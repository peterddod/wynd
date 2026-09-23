"""The runtime-owned loop over a ModelProvider, driven through `complete()` with `ScriptedModelProvider` and real
tools (SPEC §3.5, §3.9; PLAN §3.9 agentic rows, §5.5; `$DRAFTS/03 §6.5, §6.7, §6.8`)."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel, TypeAdapter

from wynd.runtime.agentic.errors import ToolInputError
from wynd.runtime.agentic.loop import StructuredCall, complete, complete_structured
from wynd.runtime.agentic.prompt import RETRY_NO_OUTPUT, RETRY_TRUNCATED, RETRY_VALIDATION, render_prompt
from wynd.runtime.cassettes import CassetteSession
from wynd.runtime.cassettes.key import Normaliser, generate_request, request_key
from wynd.runtime.errors import StepFailure
from wynd.runtime.handle import RuntimeHandle, StepCache, StepTrace
from wynd.runtime.interface import interface_of
from wynd.runtime.mcp import McpServer, snapshot
from wynd.runtime.mcp.entry import McpServerEntry
from wynd.runtime.mcp.fake_server import serve_http
from wynd.runtime.mcp.snapshot import McpToolSpec
from wynd.runtime.middleware import AgentCall, AgentResult
from wynd.runtime.policy import CassetteConfig, ExecPolicy
from wynd.runtime.providers import register_for_tests
from wynd.runtime.providers.scripted import ScriptedModelProvider
from wynd.runtime.providers.types import GenerateResponse, ProviderError, ToolCall, ToolSchema
from wynd.runtime.step import AgenticStep
from wynd.runtime.tools import env, tool
from wynd.runtime.usage import ModelInfo, Usage
from wynd.spec.lockfiles import RetryPolicy

AGENTIC = RetryPolicy(run=2, validation=2, tool=1)
MODEL = "scripted-model-2026"
RATES_ASKED: list[str] = []


class Text(BaseModel):
    text: str


class Done(BaseModel):
    exit: Literal["done"] = "done"
    total: float


class NotAnInvoice(BaseModel):
    exit: Literal["not_an_invoice"] = "not_an_invoice"


class Extract(AgenticStep):
    """Extract the invoice total in GBP. Convert foreign currencies with lookup_rate."""

    Input = Text
    Output = Done | NotAnInvoice
    context = ["process.goal"]

    @tool
    def lookup_rate(self, currency: str) -> float:
        """Look up the exchange rate of a currency to GBP."""
        RATES_ASKED.append(currency)
        return {"USD": 0.8, "EUR": 0.85}[currency]

    def run(self, input: Text) -> Done | NotAnInvoice: ...


class Guarded(AgenticStep):
    """Extract the total using the guarded tools."""

    Input = Text
    Output = Done

    @tool
    def check_code(self, code: str) -> str:
        """Check a currency code."""
        if len(code) != 3:
            raise ToolInputError("a currency code has three letters")
        return "ok"

    @tool(env=["RATES_TOKEN"])
    def premium_rate(self, currency: str) -> str:
        """Look up a rate with the premium service."""
        return env("RATES_TOKEN")

    @tool
    def fetch_rate(self, currency: str) -> str:
        """Fetch a rate over HTTP."""
        return self.runtime.http.get(f"https://rates.invalid/{currency}").text()

    def run(self, input: Text) -> Done: ...


class Triage(AgenticStep):
    """Triage the issue."""

    Input = Text
    Output = Done
    mcp = [McpServer("github", allow=["get_issue"])]

    def run(self, input: Text) -> Done: ...


@pytest.fixture
def provider():
    scripted = ScriptedModelProvider()
    undo = register_for_tests("scripted", scripted)
    RATES_ASKED.clear()
    yield scripted
    undo()


@pytest.fixture(autouse=True)
def sleeps(monkeypatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("wynd.runtime.agentic.loop.time.sleep", slept.append)
    return slept


def usage(n: int = 1) -> Usage:
    return Usage(input_tokens=100 * n, output_tokens=10 * n, cost_usd=0.001 * n, latency_ms=50.0 * n, calls=n)


def reply(*, output: Any = None, text: str = "", tool_calls: tuple[ToolCall, ...] = (), stop: str | None = None,
          model_id: str = MODEL) -> GenerateResponse:
    content: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
    content += [{"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments} for tc in tool_calls]
    return GenerateResponse(
        message={"role": "assistant", "content": content}, text=text, tool_calls=list(tool_calls),
        structured_output=output, stop=stop or ("tool_calls" if tool_calls else "end"), usage=usage(),
        model_id=model_id,
    )


def rate(currency: str, id: str = "t1") -> ToolCall:
    return ToolCall(id, "lookup_rate", {"currency": currency})


def transport(tool_called: bool = False) -> ProviderError:
    return ProviderError("529 overloaded", kind="transport", retryable=True, tool_called=tool_called)


def run(tmp_path: Path, provider: ScriptedModelProvider, script: list, *, step: type = Extract,
        retries: RetryPolicy = AGENTIC, max_turns: int = 25, mcp: list[dict[str, Any]] | None = None,
        provider_name: str = "scripted") -> tuple[AgentResult | StepFailure, list[dict[str, Any]]]:
    provider.script, provider.requests = list(script), []
    events: list[dict[str, Any]] = []
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    runtime = RuntimeHandle(run_id="run_1", step_path="extract", step_run=1, workspace=workspace,
                            logger=logging.getLogger("wynd.step.extract"), trace=StepTrace(events.append),
                            cache=StepCache())
    instance = step()
    instance.runtime = runtime
    policy = ExecPolicy(kind="agentic", retries=retries, provider=provider_name, model_id="scripted-cheap",
                        tier="cheap", thinking="low", max_turns=max_turns, mcp=mcp or [])
    call = AgentCall("extract", Text(text="INVOICE 1200 USD"), {"process.goal": "Pay invoices"}, interface_of(step),
                     policy, CassetteConfig(), runtime)
    try:
        return complete(instance, call), events
    except StepFailure as failure:
        return failure, events


def model_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == "model.call"]


def user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def first_user_message() -> dict[str, Any]:
    _, text = render_prompt(Extract.__doc__, {"process.goal": "Pay invoices"}, {"text": "INVOICE 1200 USD"})
    return user(text)


# --- tool calls ----------------------------------------------------------------------------------------------------

def test_tool_call_then_result_then_valid_output(tmp_path, provider):
    first = reply(text="Looking up the USD rate.", tool_calls=(rate("USD"),))
    result, events = run(tmp_path, provider, [first, reply(output={"exit": "done", "total": 960.0})])

    assert result.output == Done(total=960.0)
    assert result.note == "Looking up the USD rate."
    assert result.attempts == 1
    assert result.usage == usage(2)
    assert result.model == ModelInfo(provider="scripted", model_id=MODEL, tier="cheap", thinking="low")
    assert RATES_ASKED == ["USD"]

    request = provider.requests[0]
    system, _ = render_prompt(Extract.__doc__, {}, {})
    assert (request.model_id, request.thinking, request.system) == ("scripted-cheap", "low", system)
    assert request.messages == [first_user_message()]
    assert request.output_schema == interface_of(Extract).output_json_schema()      # unwrapped
    assert [t.name for t in request.tools] == ["lookup_rate"]
    assert request.tools[0].description == "Look up the exchange rate of a currency to GBP."
    assert provider.requests[1].messages == [
        first_user_message(),
        first.message,
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "0.8", "is_error": False}]},
    ]
    assert [e["type"] for e in events] == ["model.call", "tool.call", "model.call"]
    assert (events[1]["tool"], events[1]["source"], events[1]["result"], events[1]["ok"]) == (
        "lookup_rate", "method", "0.8", True)


def test_several_tool_calls_run_in_order_and_answer_in_one_message(tmp_path, provider):
    both = reply(tool_calls=(rate("EUR", "a"), rate("USD", "b")))
    result, _ = run(tmp_path, provider, [both, reply(output={"exit": "done", "total": 1.0})])
    assert isinstance(result, AgentResult)
    assert RATES_ASKED == ["EUR", "USD"]
    assert provider.requests[1].messages[-1]["content"] == [
        {"type": "tool_result", "tool_use_id": "a", "content": "0.85", "is_error": False},
        {"type": "tool_result", "tool_use_id": "b", "content": "0.8", "is_error": False},
    ]


def test_bad_tool_arguments_go_back_to_the_model(tmp_path, provider):
    bad = reply(tool_calls=(ToolCall("t1", "lookup_rate", {"code": "USD"}),))
    unknown = reply(tool_calls=(ToolCall("t2", "convert", {}),))
    result, _ = run(tmp_path, provider, [bad, unknown, reply(output={"exit": "done", "total": 1.0})])
    assert isinstance(result, AgentResult)
    assert RATES_ASKED == []
    [bad_result] = provider.requests[1].messages[-1]["content"]
    assert bad_result["is_error"] is True
    assert bad_result["content"].startswith("invalid arguments:\n")
    [unknown_result] = provider.requests[2].messages[-1]["content"]
    assert unknown_result == {"type": "tool_result", "tool_use_id": "t2", "content": "unknown tool 'convert'",
                              "is_error": True}


def test_a_tool_input_error_goes_back_to_the_model(tmp_path, provider):
    ask = reply(tool_calls=(ToolCall("t1", "check_code", {"code": "EURO"}),))
    result, events = run(tmp_path, provider, [ask, reply(output={"exit": "done", "total": 1.0})], step=Guarded)
    assert isinstance(result, AgentResult)
    assert provider.requests[1].messages[-1]["content"] == [
        {"type": "tool_result", "tool_use_id": "t1", "content": "a currency code has three letters", "is_error": True}]
    [call] = [e for e in events if e["type"] == "tool.call"]
    assert (call["ok"], call["is_error"]) == (True, True)


@pytest.mark.parametrize(("name", "message"), [
    ("premium_rate", "RATES_TOKEN is not set; it is listed in process.env.yaml — run `wynd env check`"),
    ("fetch_rate", "step does not declare effects: [network]"),
])
def test_tool_configuration_problems_are_config_errors(tmp_path, provider, monkeypatch, name, message):
    monkeypatch.delenv("RATES_TOKEN", raising=False)
    ask = reply(tool_calls=(ToolCall("t1", name, {"currency": "USD"}),))
    failure, _ = run(tmp_path, provider, [ask, reply(output={"exit": "done", "total": 1.0})], step=Guarded)
    assert (failure.cause, failure.message) == ("config", message)
    assert (failure.usage, failure.attempts) == (usage(1), 1)
    assert len(provider.requests) == 1


def test_a_tool_that_raises_is_a_tool_failure(tmp_path, provider):
    failure, events = run(tmp_path, provider, [reply(tool_calls=(rate("XXX"),)), reply(output={"exit": "done",
                                                                                               "total": 1.0})])
    assert isinstance(failure, StepFailure)
    assert failure.cause == "tool"
    assert failure.message == "tool lookup_rate failed: KeyError: 'XXX'"
    assert failure.usage == usage(1)
    assert len(provider.requests) == 1
    assert [(e["type"], e.get("ok")) for e in events] == [("model.call", None), ("tool.call", False)]


# --- validation retries continue the conversation ----------------------------------------------------------------

INVALID_TEXT = RETRY_VALIDATION.format(
    errors="- output.done.total: Input should be a valid number, unable to parse string as a number (got 'twelve')")


def test_a_validation_retry_continues_the_conversation(tmp_path, provider):
    invalid = reply(output={"exit": "done", "total": "twelve"})
    result, events = run(tmp_path, provider, [invalid, reply(output={"exit": "done", "total": 12.0})])

    assert result.output == Done(total=12.0)
    assert result.attempts == 2
    assert provider.requests[1].messages == [first_user_message(), invalid.message, user(INVALID_TEXT)]
    calls = model_calls(events)
    assert [(c["outcome"], c["attempt"], c["reason"], c["errors"]) for c in calls] == [
        ("invalid", 1, "initial", INVALID_TEXT), ("valid", 2, "validation_retry", None)]
    assert calls[0]["messages_new"] == [first_user_message()]
    assert calls[1]["messages_new"] == [invalid.message, user(INVALID_TEXT)]


def test_exhausted_validation_retries_are_an_output_validation_error(tmp_path, provider):
    invalid = [reply(output={"exit": "done", "total": f"t{i}"}) for i in range(3)]
    failure, events = run(tmp_path, provider, invalid)

    assert failure.cause == "output_validation"
    assert failure.attempts == 3
    assert failure.usage == usage(3)
    assert failure.partial_outputs == {"exit": "done", "total": "t2"}
    assert failure.message == ("no valid structured output after 3 attempt(s); it did not validate:\n"
                               "- output.done.total: Input should be a valid number, unable to parse string as a "
                               "number (got 't2')")
    assert len(provider.requests) == 3
    assert [c["attempt"] for c in model_calls(events)] == [1, 2, 3]


def test_no_validation_retries(tmp_path, provider):
    failure, _ = run(tmp_path, provider, [reply(output={"exit": "maybe"})],
                     retries=RetryPolicy(run=2, validation=0, tool=1))
    assert (failure.cause, failure.attempts) == ("output_validation", 1)


def test_a_reply_without_structured_output_is_retried(tmp_path, provider):
    chatty = reply(text="The total is 12 pounds.")
    result, events = run(tmp_path, provider, [chatty, reply(output={"exit": "done", "total": 12.0})])
    assert result.note == "The total is 12 pounds."
    assert provider.requests[1].messages == [first_user_message(), chatty.message, user(RETRY_NO_OUTPUT)]
    assert [c["outcome"] for c in model_calls(events)] == ["no_output", "valid"]


def test_a_truncated_reply_is_dropped_and_retried_keeping_alternation(tmp_path, provider):
    cut = reply(text='{"exit": "done", "tot', stop="max_tokens")
    result, events = run(tmp_path, provider, [cut, reply(output={"exit": "done", "total": 12.0})])

    assert result.attempts == 2
    retried = {"role": "user", "content": [*first_user_message()["content"], {"type": "text", "text": RETRY_TRUNCATED}]}
    assert provider.requests[1].messages == [retried]
    assert provider.requests[0].messages == [first_user_message()]      # the earlier request is not rewritten
    calls = model_calls(events)
    assert [(c["outcome"], c["errors"]) for c in calls] == [("truncated", RETRY_TRUNCATED), ("valid", None)]
    assert calls[1]["messages_new"] == [retried]


def test_a_truncated_reply_after_tool_results(tmp_path, provider):
    ask = reply(tool_calls=(rate("USD"),))
    script = [ask, reply(stop="max_tokens"), reply(output={"exit": "done", "total": 1.0})]
    result, _ = run(tmp_path, provider, script)
    assert isinstance(result, AgentResult)
    messages = provider.requests[2].messages
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[2]["content"][-1] == {"type": "text", "text": RETRY_TRUNCATED}
    assert messages[2]["content"][0]["type"] == "tool_result"


def test_truncation_counts_against_the_validation_retries(tmp_path, provider):
    failure, _ = run(tmp_path, provider, [reply(stop="max_tokens")] * 3)
    assert failure.cause == "output_validation"
    assert failure.attempts == 3
    assert failure.message == ("no valid structured output after 3 attempt(s); the reply was cut off at the "
                               "output token limit")


# --- restarts ------------------------------------------------------------------------------------------------------

def test_transport_failure_before_any_tool_call_restarts_with_fresh_messages(tmp_path, provider, sleeps):
    invalid = reply(output={"exit": "done", "total": "twelve"})
    result, events = run(tmp_path, provider, [invalid, transport(), reply(output={"exit": "done", "total": 1.0})])

    assert result.output == Done(total=1.0)
    assert result.attempts == 1
    assert result.usage == usage(2)
    assert sleeps == [1.0]
    assert provider.requests[2].messages == [first_user_message()]
    calls = model_calls(events)
    assert [(c["outcome"], c["restart"], c["reason"], c["n"]) for c in calls] == [
        ("invalid", 0, "initial", 1), ("error", 0, "validation_retry", 2), ("valid", 1, "initial", 3)]
    assert calls[1]["errors"] == "ProviderError: 529 overloaded"
    assert calls[1]["usage"] is None and calls[1]["response"] is None


def test_restarts_are_bounded_by_retries_run(tmp_path, provider, sleeps):
    failure, _ = run(tmp_path, provider, [transport(), transport(), transport(), reply(output={"exit": "done",
                                                                                                "total": 1.0})])
    assert failure.cause == "transport"
    assert failure.message == "529 overloaded"
    assert sleeps == [1.0, 4.0]
    assert len(provider.requests) == 3

    failure, _ = run(tmp_path, provider, [transport()], retries=RetryPolicy(run=0, validation=2, tool=1))
    assert failure.cause == "transport"
    assert sleeps == [1.0, 4.0]


def test_a_non_retryable_transport_error_is_not_restarted(tmp_path, provider, sleeps):
    cancelled = ProviderError("cancelled", kind="transport", retryable=False)
    failure, _ = run(tmp_path, provider, [cancelled, reply(output={"exit": "done", "total": 1.0})])
    assert (failure.cause, sleeps, len(provider.requests)) == ("transport", [], 1)


def test_transport_failure_after_a_tool_call_is_never_restarted(tmp_path, provider, sleeps):
    failure, _ = run(tmp_path, provider, [reply(tool_calls=(rate("USD"),)), transport(),
                                          reply(output={"exit": "done", "total": 1.0})])
    assert failure.cause == "transport"
    assert sleeps == []
    assert RATES_ASKED == ["USD"]                      # the side effect fired exactly once
    assert failure.usage == usage(1)


def test_a_tool_request_with_bad_arguments_also_prevents_a_restart(tmp_path, provider, sleeps):
    bad = reply(tool_calls=(ToolCall("t1", "lookup_rate", {}),))
    failure, _ = run(tmp_path, provider, [bad, transport()])
    assert (failure.cause, sleeps) == ("transport", [])


# --- model and config errors -----------------------------------------------------------------------------------------

def test_refusal_is_a_model_error(tmp_path, provider):
    failure, events = run(tmp_path, provider, [reply(text="I can't help with that.", stop="refusal")])
    assert failure.cause == "model"
    assert failure.message == "the model refused: I can't help with that."
    assert [c["outcome"] for c in model_calls(events)] == ["refusal"]


@pytest.mark.parametrize(("kind", "cause"), [
    ("auth", "config"), ("unavailable", "config"), ("invalid_request", "model"), ("max_turns", "model"),
])
def test_provider_error_kinds_map_to_causes(tmp_path, provider, sleeps, kind, cause):
    failure, _ = run(tmp_path, provider, [ProviderError(f"{kind} problem", kind=kind, retryable=False)])
    assert (failure.cause, failure.message, sleeps) == (cause, f"{kind} problem", [])


def test_exceeding_max_turns_is_a_model_error(tmp_path, provider):
    asks = [reply(tool_calls=(rate("USD", f"t{i}"),)) for i in range(3)]
    failure, _ = run(tmp_path, provider, asks, max_turns=2)
    assert failure.cause == "model"
    assert failure.message == "agent loop exceeded max_turns=2"
    assert len(provider.requests) == 2


def test_an_unknown_provider_is_a_config_error(tmp_path, provider):
    failure, events = run(tmp_path, provider, [], provider_name="no-such-provider")
    assert failure.cause == "config"
    assert failure.message == "provider 'no-such-provider' is not installed (entry point group wynd.providers)"
    assert events == []


# --- model.call events ---------------------------------------------------------------------------------------------

def test_model_call_event_fields(tmp_path, provider):
    result, events = run(tmp_path, provider, [reply(output={"exit": "not_an_invoice"})])
    assert result.output == NotAnInvoice()
    [event] = events
    request = provider.requests[0]
    key = request_key(generate_request(request, provider="scripted", tier="cheap"),
                      Normaliser.for_run("run_1", tmp_path / "ws"))
    assert event == {
        "type": "model.call",
        "step": "extract",
        "provider": "scripted",
        "kind": "model",
        "tier": "cheap",
        "thinking": "low",
        "model_id": MODEL,
        "n": 1,
        "attempt": 1,
        "restart": 0,
        "reason": "initial",
        "outcome": "valid",
        "errors": None,
        "request_hash": key[:32],
        "request": {"system": request.system, "tools": [asdict(t) for t in request.tools],
                    "output_schema": request.output_schema},
        "messages_new": [first_user_message()],
        "response": {"text": "", "tool_calls": [], "structured_output": {"exit": "not_an_invoice"}, "stop": "end"},
        "usage": usage().model_dump(mode="json"),
        "startup_ms": None,
        "cost_basis": None,
        "cassette": "live",
    }
    assert re.fullmatch(r"[0-9a-f]{32}", event["request_hash"])


# --- MCP -----------------------------------------------------------------------------------------------------------

def mcp_block(url: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    schema = schema or {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]}
    snap = snapshot(McpServer("github", allow=["get_issue"]),
                    [McpToolSpec("get_issue", "Get a GitHub issue by number.", schema, {"readOnlyHint": True})])
    entry = McpServerEntry(name="github", transport="http", url=url,
                           headers={"Authorization": "Bearer ${env:GITHUB_TOKEN}"}, auth_env=["GITHUB_TOKEN"])
    return {**snap.model_dump(mode="json"), "entry": entry.model_dump(mode="json")}


def test_mcp_tools_are_called_through_the_runtime_loop(tmp_path, provider, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    with serve_http(token="gh-secret") as server:
        ask = reply(tool_calls=(ToolCall("t1", "github__get_issue", {"number": 42}),))
        result, events = run(tmp_path, provider, [ask, reply(output={"exit": "done", "total": 1.0})], step=Triage,
                             mcp=[mcp_block(server.url)])
    assert isinstance(result, AgentResult)
    assert provider.requests[0].tools == [ToolSchema(
        "github__get_issue", "Get a GitHub issue by number.",
        {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]})]
    [tool_result] = provider.requests[1].messages[-1]["content"]
    assert '"number": 42' in tool_result["content"] and tool_result["is_error"] is False
    assert [e["source"] for e in events if e["type"] == "tool.call"] == ["mcp:github"]


def test_a_missing_mcp_env_var_fails_before_any_model_call(tmp_path, provider):
    failure, events = run(tmp_path, provider, [reply(output={"exit": "done", "total": 1.0})], step=Triage,
                          mcp=[mcp_block("http://127.0.0.1:9/mcp")])
    assert failure.cause == "config"
    assert "needs env var GITHUB_TOKEN" in failure.message
    assert provider.requests == [] and events == []


def test_an_mcp_snapshot_mismatch_fails_before_any_model_call(tmp_path, provider, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    changed = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    with serve_http(token="gh-secret") as server:
        failure, _ = run(tmp_path, provider, [reply(output={"exit": "done", "total": 1.0})], step=Triage,
                         mcp=[mcp_block(server.url, changed)])
    assert failure.cause == "config"
    assert failure.message == (
        "MCP server 'github' no longer matches the snapshot in step.lock.yaml:\n"
        "  - get_issue: input schema changed\n"
        "Re-run `wynd compile` to re-snapshot, and review the diff."
    )
    assert provider.requests == []


# --- complete_structured without a step (the M5 edge-check path) ------------------------------------------------------

class Verdict(BaseModel):
    exit: Literal["done"] = "done"
    take: bool
    reason: str


def test_complete_structured_without_tools(tmp_path, provider):
    provider.script = [reply(output={"exit": "done", "take": True, "reason": "the total is positive"})]
    events: list[dict[str, Any]] = []
    runtime = RuntimeHandle(run_id="run_1", step_path="edge:validate.done[save]", step_run=1, workspace=tmp_path,
                            logger=logging.getLogger("wynd.edge"), trace=StepTrace(events.append), cache=StepCache())
    policy = ExecPolicy(kind="agentic", retries=RetryPolicy(validation=2), provider="scripted", model_id="m",
                        tier="cheap", thinking="low")
    adapter = TypeAdapter(Verdict)
    result = complete_structured(StructuredCall(
        unit="edge:validate.done[save]", instruction="Decide whether to take the transition.", context={},
        input={"bindings": {"total": 12}}, adapter=adapter, output_schema=adapter.json_schema(), tools=None,
        policy=policy, runtime=runtime,
        cassette=CassetteSession(CassetteConfig(), run_id="run_1", workspace=tmp_path, provider="scripted",
                                 tier="cheap", thinking="low", step="edge:validate.done[save]"),
    ))
    assert result.output == Verdict(take=True, reason="the total is positive")
    [request] = provider.requests
    assert request.tools == []
    assert request.messages == [user('# Input\n```json\n{\n  "bindings": {\n    "total": 12\n  }\n}\n```')]
    assert [(e["type"], e["step"]) for e in events] == [("model.call", "edge:validate.done[save]")]

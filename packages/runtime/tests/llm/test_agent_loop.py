"""The loop around an AgentProvider harness, driven through `complete()` with `ScriptedAgentProvider` (which, like a
real harness, runs the tools its transcript says it called) and real tools (SPEC §3.5, §3.9; PLAN §3.9 agentic rows,
§5.5; `$DRAFTS/03 §6.6–§6.8`)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel

from wynd.runtime.agentic.checks import step_instruction
from wynd.runtime.agentic.loop import complete
from wynd.runtime.agentic.prompt import RETRY_NO_OUTPUT, RETRY_VALIDATION
from wynd.runtime.cassettes.key import Normaliser, agent_request, request_key
from wynd.runtime.errors import StepFailure
from wynd.runtime.handle import RuntimeHandle, StepCache, StepTrace
from wynd.runtime.interface import interface_of
from wynd.runtime.middleware import AgentCall, AgentResult
from wynd.runtime.policy import CassetteConfig, ExecPolicy
from wynd.runtime.providers import register_for_tests
from wynd.runtime.providers.scripted import ScriptedAgentProvider
from wynd.runtime.providers.types import AgentResponse, ProviderError
from wynd.runtime.step import AgenticStep
from wynd.runtime.tools import tool
from wynd.runtime.usage import ModelInfo, Usage
from wynd.spec.lockfiles import RetryPolicy

AGENTIC = RetryPolicy(run=2, validation=2, tool=1)
MODEL = "scripted-agent-2026"
GOAL = {"process.goal": "Pay invoices"}
RATES_ASKED: list[str] = []


class Text(BaseModel):
    text: str


class Done(BaseModel):
    exit: Literal["done"] = "done"
    total: float


class NotAnInvoice(BaseModel):
    exit: Literal["not_an_invoice"] = "not_an_invoice"


class Extract(AgenticStep):
    """Extract the invoice total in GBP.

    Convert foreign currencies with lookup_rate."""

    Input = Text
    Output = Done | NotAnInvoice
    context = ["process.goal"]

    @tool
    def lookup_rate(self, currency: str) -> float:
        """Look up the exchange rate of a currency to GBP."""
        RATES_ASKED.append(currency)
        return {"USD": 0.8}[currency]

    def run(self, input: Text) -> Done | NotAnInvoice: ...


@pytest.fixture
def provider():
    scripted = ScriptedAgentProvider()
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


def answer(output: Any = None, *, note: str = "", transcript: list[dict[str, Any]] | None = None,
           tool_calls: int = 0, session: dict[str, Any] | None = None) -> AgentResponse:
    return AgentResponse(structured_output=output, usage=usage(), model_id=MODEL, transcript=transcript or [],
                         session=session or {"sid": "s1"}, note=note, tool_calls=tool_calls, startup_ms=600.0)


def rate_use(currency: str, id: str = "t1") -> list[dict[str, Any]]:
    return [{"type": "tool_use", "id": id, "name": "lookup_rate", "input": {"currency": currency}},
            {"type": "tool_result", "id": id, "is_error": False, "content": "0.8"}]


def transport(tool_called: bool = False) -> ProviderError:
    return ProviderError("claude-code transport failure", kind="transport", retryable=True, tool_called=tool_called)


VALID = {"exit": "done", "total": 960.0}
INVALID_TEXT = RETRY_VALIDATION.format(
    errors="- output.done.total: Input should be a valid number, unable to parse string as a number (got 'twelve')")


def run(tmp_path: Path, provider: ScriptedAgentProvider, script: list, *, retries: RetryPolicy = AGENTIC,
        ) -> tuple[AgentResult | StepFailure, list[dict[str, Any]]]:
    provider.script, provider.requests = list(script), []
    events: list[dict[str, Any]] = []
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    runtime = RuntimeHandle(run_id="run_1", step_path="extract", step_run=1, workspace=workspace,
                            logger=logging.getLogger("wynd.step.extract"), trace=StepTrace(events.append),
                            cache=StepCache())
    instance = Extract()
    instance.runtime = runtime
    policy = ExecPolicy(kind="agentic", retries=retries, provider="scripted", model_id="scripted-cheap", tier="cheap",
                        thinking="low", builtin_tools=["Read"], max_turns=12)
    call = AgentCall("extract", Text(text="INVOICE 1200 USD"), dict(GOAL), interface_of(Extract), policy,
                     CassetteConfig(), runtime)
    try:
        return complete(instance, call), events
    except StepFailure as failure:
        return failure, events


def model_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == "model.call"]


def test_the_request_carries_the_step(tmp_path, provider):
    result, _ = run(tmp_path, provider, [answer(VALID, note="Converted at 0.8.")])

    assert result.output == Done(total=960.0)
    assert (result.note, result.attempts, result.usage) == ("Converted at 0.8.", 1, usage(1))
    assert result.model == ModelInfo(provider="scripted", model_id=MODEL, tier="cheap", thinking="low")
    [req] = provider.requests
    assert (req.model_id, req.thinking) == ("scripted-cheap", "low")
    assert req.instruction == step_instruction(Extract) == (
        "Extract the invoice total in GBP.\n\nConvert foreign currencies with lookup_rate.")
    assert (req.context, req.input) == (GOAL, {"text": "INVOICE 1200 USD"})
    assert req.output_schema == interface_of(Extract).output_json_schema()           # unwrapped
    assert [(h.name, h.local) for h in req.tools] == [("lookup_rate", True)]
    assert (req.mcp_servers, req.workspace, req.max_turns, req.builtin_tools) == ([], tmp_path / "ws", 12, ["Read"])
    assert (req.continuation, req.prompt) == (None, None)


def test_harness_tool_calls_run_through_the_tool_set_and_built_ins_are_traced(tmp_path, provider):
    transcript = [*rate_use("USD"),
                  {"type": "tool_use", "id": "b1", "name": "Read", "input": {"file_path": "a.txt"}},
                  {"type": "tool_result", "id": "b1", "is_error": False, "content": "hello"}]
    result, events = run(tmp_path, provider, [answer(VALID, transcript=transcript, tool_calls=2)])
    assert isinstance(result, AgentResult)
    assert RATES_ASKED == ["USD"]
    assert [(e["type"], e.get("tool"), e.get("source")) for e in events] == [
        ("tool.call", "lookup_rate", "method"), ("tool.call", "Read", "harness"), ("model.call", None, None)]


def test_a_validation_retry_continues_the_session(tmp_path, provider):
    invalid = answer({"exit": "done", "total": "twelve"}, session={"sid": "s1", "entries": [1, 2]})
    result, events = run(tmp_path, provider, [invalid, answer(VALID)])

    assert result.attempts == 2
    first, second = provider.requests
    assert first.continuation is None
    assert second.continuation.message == INVALID_TEXT
    assert second.continuation.session == {"sid": "s1", "entries": [1, 2]}
    assert (second.instruction, second.input) == (first.instruction, first.input)
    calls = model_calls(events)
    assert [(c["outcome"], c["attempt"], c["reason"], c["request"]["continuation"]) for c in calls] == [
        ("invalid", 1, "initial", None), ("valid", 2, "validation_retry", INVALID_TEXT)]
    assert calls[0]["errors"] == INVALID_TEXT


def test_replies_without_structured_output_exhaust_the_retries(tmp_path, provider):
    failure, _ = run(tmp_path, provider, [answer(None)] * 3)
    assert (failure.cause, failure.attempts, failure.partial_outputs) == ("output_validation", 3, None)
    assert failure.message == "no valid structured output after 3 attempt(s); the reply had no structured output"


def test_a_reply_without_structured_output_continues_the_session(tmp_path, provider):
    result, events = run(tmp_path, provider, [answer(None, note="I could not find a total."), answer(VALID)])
    assert result.attempts == 2
    assert provider.requests[1].continuation.message == RETRY_NO_OUTPUT
    assert [c["outcome"] for c in model_calls(events)] == ["no_output", "valid"]


def test_exhausted_validation_retries(tmp_path, provider):
    failure, _ = run(tmp_path, provider, [answer({"exit": "maybe"})] * 3)
    assert failure.cause == "output_validation"
    assert (failure.attempts, failure.usage, failure.partial_outputs) == (3, usage(3), {"exit": "maybe"})
    assert failure.message.startswith("no valid structured output after 3 attempt(s); it did not validate:\n- output:")
    assert len(provider.requests) == 3


def test_usage_is_summed_and_the_note_is_the_valid_replys(tmp_path, provider):
    result, _ = run(tmp_path, provider, [answer({"exit": "done"}, note="first"), answer(VALID, note="second")])
    assert (result.note, result.usage) == ("second", usage(2))


# --- restarts ------------------------------------------------------------------------------------------------------

def test_restart_when_no_tool_was_called_anywhere(tmp_path, provider, sleeps):
    invalid = answer({"exit": "done", "total": "twelve"})
    result, events = run(tmp_path, provider, [invalid, transport(), answer(VALID)])

    assert result.output == Done(total=960.0)
    assert result.attempts == 1
    assert sleeps == [1.0]
    assert provider.requests[1].continuation is not None
    assert provider.requests[2].continuation is None                   # a full restart: a fresh conversation
    assert [(c["outcome"], c["restart"]) for c in model_calls(events)] == [("invalid", 0), ("error", 0), ("valid", 1)]


def test_restarts_are_bounded_by_retries_run(tmp_path, provider, sleeps):
    failure, _ = run(tmp_path, provider, [transport()] * 3 + [answer(VALID)])
    assert (failure.cause, failure.message, sleeps) == ("transport", "claude-code transport failure", [1.0, 4.0])
    result, _ = run(tmp_path, provider, [transport(), answer(VALID)], retries=RetryPolicy(run=1, validation=2, tool=1))
    assert isinstance(result, AgentResult)


def test_no_restart_when_the_provider_reports_a_tool_call(tmp_path, provider, sleeps):
    failure, _ = run(tmp_path, provider, [transport(tool_called=True), answer(VALID)])
    assert (failure.cause, sleeps, len(provider.requests)) == ("transport", [], 1)


def test_no_restart_after_the_tool_set_ran_a_tool(tmp_path, provider, sleeps):
    invalid = answer({"exit": "done", "total": "twelve"}, transcript=rate_use("USD"))
    failure, _ = run(tmp_path, provider, [invalid, transport(), answer(VALID)])
    assert (failure.cause, sleeps, RATES_ASKED) == ("transport", [], ["USD"])


def test_no_restart_after_harness_built_ins_ran(tmp_path, provider, sleeps):
    read = [{"type": "tool_use", "id": "b1", "name": "Read", "input": {"file_path": "a.txt"}}]
    failure, _ = run(tmp_path, provider, [answer({"exit": "done"}, transcript=read, tool_calls=1), transport(),
                                          answer(VALID)])
    assert (failure.cause, sleeps) == ("transport", [])


# --- errors --------------------------------------------------------------------------------------------------------

def test_a_tool_that_raises_aborts_the_harness(tmp_path, provider):
    failure, events = run(tmp_path, provider, [answer(VALID, transcript=rate_use("XXX"))])
    assert failure.cause == "tool"
    assert failure.message == "tool lookup_rate failed: KeyError: 'XXX'"
    assert [(e["type"], e.get("outcome"), e.get("ok")) for e in events] == [
        ("tool.call", None, False), ("model.call", "error", None)]
    assert model_calls(events)[0]["errors"] == "ToolFailure: tool lookup_rate failed: KeyError: 'XXX'"


@pytest.mark.parametrize(("kind", "cause"), [
    ("auth", "config"), ("unavailable", "config"), ("invalid_request", "model"), ("refusal", "model"),
    ("max_turns", "model"),
])
def test_provider_error_kinds_map_to_causes(tmp_path, provider, sleeps, kind, cause):
    failure, _ = run(tmp_path, provider, [ProviderError(f"{kind} problem", kind=kind, retryable=False)])
    assert (failure.cause, failure.message, sleeps) == (cause, f"{kind} problem", [])


def test_model_call_event_fields(tmp_path, provider):
    transcript = [{"type": "text", "text": "Done."}]
    _, events = run(tmp_path, provider, [answer(VALID, note="Done.", transcript=transcript)])
    [req] = provider.requests
    key = request_key(agent_request(req, provider="scripted", tier="cheap"), Normaliser.for_run("run_1", req.workspace))
    [event] = events
    assert event == {
        "type": "model.call",
        "step": "extract",
        "provider": "scripted",
        "kind": "agent",
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
        "request": {
            "instruction": step_instruction(Extract),
            "context": GOAL,
            "input": {"text": "INVOICE 1200 USD"},
            "output_schema": interface_of(Extract).output_json_schema(),
            "tools": [{"name": "lookup_rate", "description": "Look up the exchange rate of a currency to GBP.",
                       "input_schema": req.tools[0].input_schema}],
            "mcp": [],
            "builtin_tools": ["Read"],
            "max_turns": 12,
            "continuation": None,
        },
        "messages_new": None,
        "response": {"structured_output": VALID, "note": "Done.", "transcript": transcript},
        "usage": usage().model_dump(mode="json"),
        "startup_ms": 600.0,
        "cost_basis": None,
        "cassette": "live",
    }

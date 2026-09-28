"""M5 agentic-edge checks (PLAN §5.6, §3.12 `edge.check`; `$DRAFTS/08 §4.4–§4.5, §4.8`).

`run_edge_check` over both provider kinds (take, not take, validation retries that continue the conversation,
exhausted retries, transport and other provider failures, cassettes record -> replay -> miss, usage, `model.call`
under the `edge:` unit), the worker handler `handle_edge_check`, and `WorkerEdgeChecker` against a pool double and
against real `sys.executable` workers running the `fake` provider (incl. a timeout that kills the worker)."""

from __future__ import annotations

import io
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from wynd.runtime.agentic.prompt import RETRY_VALIDATION, render_prompt
from wynd.runtime.cassettes import NO_RECORDING
from wynd.runtime.edges import (
    EDGE_VERDICT_SCHEMA,
    VERIFIER_INSTRUCTION,
    EdgeCheckCall,
    EdgeCheckError,
    EdgeCheckResult,
    EdgeVerdict,
    WorkerEdgeChecker,
    handle_edge_check,
    run_edge_check,
)
from wynd.runtime.policy import CassetteConfig
from wynd.runtime.providers import register_for_tests
from wynd.runtime.providers.scripted import ScriptedAgentProvider, ScriptedModelProvider
from wynd.runtime.providers.types import AgentResponse, GenerateResponse, ProviderError
from wynd.runtime.usage import Usage
from wynd.runtime.worker import server as worker_server
from wynd.runtime.worker.client import StepTimeout, WorkerCrashed, WorkerRpcError
from wynd.runtime.worker.pool import WorkerPool
from wynd.runtime.worker.protocol import EDGE_CHECK_ERROR, INTERNAL_ERROR, INVALID_PARAMS
from wynd.spec.lockfiles import EdgeLockEntry, check_hash
from wynd.spec.plan import PlanVenv, RunPlan

MODEL = "scripted-2026"
CHECK = "The document is a final supplier invoice that requests payment."
GOAL = "Pay only real invoices."
CONTEXT = {"steps.read.outputs": {"text": "PRO FORMA INVOICE PF-88", "pages": 1}}
BINDINGS = {"record": {"key": "umbrella__PF-88"}, "amount": 120}
INPUT = {"process_goal": GOAL, "transition": {"from": "validate.done", "to": "save"}, "bindings": BINDINGS}
KEY = "validate.done[save]"
SCRIPT_VAR = "WYND_FAKE_PROVIDER_SCRIPT"
CALL_JSON = TypeAdapter(EdgeCheckCall)


def edge_call(*, check: str = CHECK, provider: str = "scripted", model_id: str = "scripted-cheap", retries: int = 2,
              run_id: str = "run-1", branch_key: str = KEY, bindings: dict[str, Any] | None = None) -> EdgeCheckCall:
    return EdgeCheckCall(
        run_id=run_id, process="p", process_goal=GOAL, edge="validate.done", branch=0, branch_key=branch_key,
        source_step="validate", target="save", check=check, context=CONTEXT,
        bindings=BINDINGS if bindings is None else bindings,
        lock=EdgeLockEntry(check_hash=check_hash(check, ["steps.read.outputs"]), retries=retries),
        provider=provider, model_id=model_id,
    )


def usage(n: int = 1) -> Usage:
    return Usage(input_tokens=100 * n, output_tokens=10 * n, cost_usd=0.001 * n, latency_ms=50.0 * n, calls=n)


def user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


@dataclass
class Scripted:
    """A registered scripted provider of one kind ("model" or "agent") and its response builder."""

    kind: str
    provider: ScriptedModelProvider | ScriptedAgentProvider

    def answer(self, output: Any) -> GenerateResponse | AgentResponse:
        if self.kind == "model":
            content = [{"type": "text", "text": json.dumps(output)}]
            return GenerateResponse(message={"role": "assistant", "content": content}, text="", tool_calls=[],
                                    structured_output=output, stop="end", usage=usage(), model_id=MODEL)
        return AgentResponse(structured_output=output, usage=usage(), model_id=MODEL, transcript=[],
                             session={"sid": "s1"})

    def load(self, *script: Any) -> None:
        self.provider.script, self.provider.requests = list(script), []


@pytest.fixture(params=["model", "agent"])
def scripted(request):
    provider = ScriptedModelProvider() if request.param == "model" else ScriptedAgentProvider()
    undo = register_for_tests("scripted", provider)
    yield Scripted(request.param, provider)
    undo()


@pytest.fixture
def model_provider():
    provider = ScriptedModelProvider()
    undo = register_for_tests("scripted", provider)
    yield Scripted("model", provider)
    undo()


@pytest.fixture(autouse=True)
def sleeps(monkeypatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr("wynd.runtime.agentic.loop.time.sleep", slept.append)
    return slept


def check(workspace: Path, call: EdgeCheckCall, cassette: CassetteConfig | None = None,
          ) -> tuple[EdgeCheckResult | EdgeCheckError, list[dict[str, Any]]]:
    workspace.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    try:
        return run_edge_check(call, cassette or CassetteConfig(), str(workspace), events.append), events
    except EdgeCheckError as err:
        return err, events


# --- verdicts ------------------------------------------------------------------------------------------------------

def test_the_verifier_sees_the_check_its_context_and_the_transition(tmp_path, scripted):
    scripted.load(scripted.answer({"take": True, "reason": "It requests payment by 1 October."}))

    result, _ = check(tmp_path / "ws", edge_call())

    assert result == EdgeCheckResult(
        verdict=EdgeVerdict(take=True, reason="It requests payment by 1 October."), attempts=1, validation_failures=0,
        provider="scripted", tier="cheap", model_id=MODEL, usage=usage(1), replayed=False,
        duration_ms=result.duration_ms)
    assert result.duration_ms > 0
    instruction = VERIFIER_INSTRUCTION.format(check=CHECK)
    assert f"\nCheck: {CHECK}\n" in instruction
    [req] = scripted.provider.requests
    assert req.output_schema == EDGE_VERDICT_SCHEMA
    assert (req.model_id, req.thinking, req.tools) == ("scripted-cheap", "low", [])
    if scripted.kind == "model":
        system, text = render_prompt(instruction, CONTEXT, INPUT)
        assert (req.system, req.messages) == (system, [user(text)])
    else:
        assert (req.instruction, req.context, req.input) == (instruction, CONTEXT, INPUT)
        assert (req.mcp_servers, req.builtin_tools, req.workspace) == ([], [], tmp_path / "ws")


def test_a_negative_verdict_is_a_result_not_an_error(tmp_path, scripted):
    scripted.load(scripted.answer({"take": False, "reason": "The document says PRO FORMA."}))

    result, _ = check(tmp_path / "ws", edge_call())

    assert result.verdict == EdgeVerdict(take=False, reason="The document says PRO FORMA.")


def test_an_invalid_verdict_continues_the_conversation(tmp_path, scripted):
    invalid = scripted.answer({"take": "yes", "reason": "It is an invoice."})
    scripted.load(invalid, scripted.answer({"take": True, "reason": "It is an invoice."}))

    result, events = check(tmp_path / "ws", edge_call())

    assert (result.verdict.take, result.attempts, result.validation_failures, result.usage) == (True, 2, 1, usage(2))
    retry = RETRY_VALIDATION.format(errors="- output.take: Input should be a valid boolean (got 'yes')")
    first, second = scripted.provider.requests
    if scripted.kind == "model":
        assert second.messages == [first.messages[0], invalid.message, user(retry)]
    else:
        assert (first.continuation, second.continuation.message) == (None, retry)
        assert second.continuation.session == {"sid": "s1"}
    assert [(e["outcome"], e["reason"]) for e in events] == [("invalid", "initial"), ("valid", "validation_retry")]


@pytest.mark.parametrize("retries", [0, 1])
def test_exhausted_validation_retries_are_a_validation_error(tmp_path, scripted, retries):
    missing = scripted.answer({"take": True})
    too_long = scripted.answer({"take": True, "reason": "x" * 501})
    scripted.load(missing, too_long, scripted.answer({"take": True, "reason": "unused"}))

    error, _ = check(tmp_path / "ws", edge_call(retries=retries))

    assert error.cause == "validation"
    assert error.message.startswith(f"no valid structured output after {retries + 1} attempt(s); it did not validate:")
    assert len(scripted.provider.requests) == retries + 1


def test_transport_failures_restart_the_check_then_fail(tmp_path, scripted, sleeps):
    down = ProviderError("529 overloaded", kind="transport", retryable=True)
    scripted.load(down, down, down, scripted.answer({"take": True, "reason": "unused"}))

    error, events = check(tmp_path / "ws", edge_call())

    assert (error.cause, error.message, sleeps) == ("transport", "529 overloaded", [1.0, 4.0])
    assert [e["outcome"] for e in events] == ["error", "error", "error"]

    scripted.load(down, scripted.answer({"take": False, "reason": "Pro forma."}))
    result, _ = check(tmp_path / "ws", edge_call())
    assert (result.verdict.take, result.attempts) == (False, 1)


@pytest.mark.parametrize(("kind", "cause"), [
    ("auth", "config"), ("unavailable", "config"), ("refusal", "model"), ("invalid_request", "model"),
    ("max_turns", "model"),
])
def test_provider_failures_map_to_edge_check_causes(tmp_path, scripted, sleeps, kind, cause):
    scripted.load(ProviderError(f"{kind} problem", kind=kind, retryable=False))

    error, _ = check(tmp_path / "ws", edge_call())

    assert (error.cause, error.message, sleeps) == (cause, f"{kind} problem", [])


def test_an_unknown_provider_is_a_config_error(tmp_path):
    error, events = check(tmp_path / "ws", edge_call(provider="no-such-provider"))

    assert (error.cause, events) == ("config", [])
    assert "no-such-provider" in error.message


def test_model_calls_are_traced_under_the_edge_unit(tmp_path, scripted):
    scripted.load(scripted.answer({"take": True, "reason": "Final invoice."}))

    _, events = check(tmp_path / "ws", edge_call())

    [event] = events
    assert {k: event[k] for k in ("type", "step", "provider", "kind", "tier", "thinking", "model_id", "n", "attempt",
                                  "outcome", "cassette")} == {
        "type": "model.call", "step": "edge:validate.done[save]", "provider": "scripted", "kind": scripted.kind,
        "tier": "cheap", "thinking": "low", "model_id": MODEL, "n": 1, "attempt": 1, "outcome": "valid",
        "cassette": "live"}
    assert event["usage"] == usage(1).model_dump(mode="json")


# --- cassettes -----------------------------------------------------------------------------------------------------

def test_record_then_replay_gives_the_same_verdict(tmp_path, scripted):
    staging = tmp_path / "staging"
    scripted.load(scripted.answer({"take": False, "reason": "The document says PRO FORMA."}))
    recorded, _ = check(tmp_path / "ws1", edge_call(), CassetteConfig(mode="record", record_dir=str(staging)))
    assert len(list(staging.glob("*.json"))) == 1

    scripted.load()                                  # any provider call now fails the test
    replayed, events = check(tmp_path / "ws2", edge_call(run_id="run-2"),
                             CassetteConfig(mode="replay", dir=str(staging)))

    assert replayed.verdict == recorded.verdict == EdgeVerdict(take=False, reason="The document says PRO FORMA.")
    assert (replayed.usage, replayed.model_id, replayed.replayed, recorded.replayed) == (usage(1), MODEL, True, False)
    assert scripted.provider.requests == []
    assert [(e["step"], e["cassette"], e["outcome"]) for e in events] == [("edge:validate.done[save]", "replay",
                                                                             "valid")]


def test_an_edited_check_misses_in_replay_with_the_explicit_error(tmp_path, scripted):
    staging = tmp_path / "staging"
    scripted.load(scripted.answer({"take": True, "reason": "Final invoice."}))
    check(tmp_path / "ws1", edge_call(), CassetteConfig(mode="record", record_dir=str(staging)))

    edited = "The document is a final supplier invoice that requests payment in GBP."
    error, _ = check(tmp_path / "ws2", edge_call(check=edited), CassetteConfig(mode="replay", dir=str(staging)))

    assert error.cause == "cassette_miss"
    assert error.message.startswith(NO_RECORDING + "\n  key: ")
    assert f"\n  cassettes: {staging}\n  request: " in error.message
    [dump] = (tmp_path / "ws2" / ".wynd" / "cassettes" / "misses").glob("*.request.json")
    assert edited in dump.read_text(encoding="utf-8")


def test_record_mode_without_a_record_dir_is_a_config_error(tmp_path, scripted):
    error, _ = check(tmp_path / "ws", edge_call(), CassetteConfig(mode="record"))

    assert (error.cause, scripted.provider.requests) == ("config", [])
    assert "record_dir" in error.message


# --- the worker handler --------------------------------------------------------------------------------------------

def handler_params(workspace: Path, call: EdgeCheckCall | None = None) -> dict[str, Any]:
    params = {"call": CALL_JSON.dump_python(call or edge_call(), mode="json"), "cassette": {"mode": "live"},
              "workspace": str(workspace)}
    return json.loads(json.dumps(params))                # exactly what arrives over the wire


def test_the_handler_answers_json_and_notifies_the_request_in_flight(tmp_path, model_provider, monkeypatch):
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(worker_server, "notify", sent.append)
    model_provider.load(model_provider.answer({"take": False, "reason": "Pro forma."}))

    reply = handle_edge_check(handler_params(tmp_path))

    assert reply == {
        "verdict": {"take": False, "reason": "Pro forma."}, "attempts": 1, "validation_failures": 0,
        "provider": "scripted", "tier": "cheap", "model_id": MODEL, "usage": usage(1).model_dump(mode="json"),
        "replayed": False, "duration_ms": reply["duration_ms"]}
    assert json.loads(json.dumps(reply)) == reply
    assert [(e["type"], e["step"]) for e in sent] == [("model.call", "edge:validate.done[save]")]


@pytest.mark.parametrize("params", [
    {},
    {"call": {"run_id": "r"}, "workspace": "/ws"},
    {"call": "not a call", "cassette": {"mode": "live"}, "workspace": "/ws"},
])
def test_the_handler_rejects_malformed_params(params):
    with pytest.raises(worker_server.RpcFault) as caught:
        handle_edge_check(params)

    assert caught.value.code == INVALID_PARAMS
    assert caught.value.message.startswith("invalid edge.check params:")


def test_a_failed_check_is_a_minus_32001_reply_of_the_worker_server(tmp_path, model_provider, monkeypatch):
    out = io.StringIO()
    server = worker_server.WorkerServer(out)
    monkeypatch.setattr(worker_server, "_active", server)
    server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "init", "params": {"protocol": 1,
                                                                                            "steps": []}}))
    model_provider.load(ProviderError("the model refused", kind="refusal", retryable=False))
    request = {"jsonrpc": "2.0", "id": 2, "method": "edge.check", "params": handler_params(tmp_path)}

    reply = server.handle_line(json.dumps(request))

    assert reply == {"jsonrpc": "2.0", "id": 2, "error": {"code": EDGE_CHECK_ERROR, "message": "the model refused",
                                                          "data": {"cause": "model", "message": "the model refused"}}}
    [notification] = [json.loads(line) for line in out.getvalue().splitlines()]
    assert notification["method"] == "event"
    assert (notification["params"]["type"], notification["params"]["outcome"]) == ("model.call", "error")


# --- WorkerEdgeChecker ---------------------------------------------------------------------------------------------

def edge_plan(tmp_path: Path, *, keys: tuple[str, ...] = (f"p:{KEY}",)) -> RunPlan:
    return RunPlan(
        mode="local", root="p", provider="fake", venv_root=str(tmp_path / "venvs"),
        venvs=[PlanVenv(id="edges", python=sys.executable, steps=[])], steps={}, processes={},
        edge_venvs={key: "edges" for key in keys},
    )


@dataclass
class PoolDouble:
    """`WorkerPool.call` double: returns `reply`, or raises it when it is an exception."""

    reply: Any
    calls: list[tuple[str, str, dict[str, Any], Any, float | None]] = field(default_factory=list)

    def call(self, venv_id, method, params, *, on_event, timeout):
        self.calls.append((venv_id, method, params, on_event, timeout))
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


RESULT_JSON = {
    "verdict": {"take": True, "reason": "Final invoice."}, "attempts": 2, "validation_failures": 1,
    "provider": "fake", "tier": "cheap", "model_id": "fake", "usage": {"calls": 2, "cost_usd": 0.0},
    "replayed": True, "duration_ms": 8.5,
}


def test_the_checker_sends_edge_check_to_the_venv_of_the_branch(tmp_path):
    pool = PoolDouble(RESULT_JSON)
    call = edge_call(provider="fake", model_id="fake")
    cassette = CassetteConfig(mode="replay", dir="/proc/cassettes/edges", literals={"/abs/tmp": "<tmp>"})
    events: list[dict[str, Any]] = []

    result = WorkerEdgeChecker(pool, edge_plan(tmp_path)).check(call, cassette=cassette, workspace="/ws/run-1",
                                                                timeout_s=30.0, on_event=events.append)

    assert result == EdgeCheckResult(
        verdict=EdgeVerdict(take=True, reason="Final invoice."), attempts=2, validation_failures=1, provider="fake",
        tier="cheap", model_id="fake", usage=Usage(calls=2, cost_usd=0.0), replayed=True, duration_ms=8.5)
    [(venv, method, params, on_event, timeout)] = pool.calls
    assert (venv, method, timeout) == ("edges", "edge.check", 30.0)
    assert on_event == events.append
    assert params == {"call": CALL_JSON.dump_python(call, mode="json"), "cassette": cassette.model_dump(mode="json"),
                      "workspace": "/ws/run-1"}
    assert json.loads(json.dumps(params)) == params
    assert events == []                              # only the executor emits edge.check


@pytest.mark.parametrize(("raised", "cause", "fragments"), [
    (WorkerRpcError("x", code=EDGE_CHECK_ERROR, data={"cause": "cassette_miss", "message": NO_RECORDING}),
     "cassette_miss", [NO_RECORDING]),
    (StepTimeout("worker did not answer edge.check within 2.5s"), "timeout", ["no verdict within 2.5s"]),
    (WorkerCrashed("worker exited with code -9"), "transport", ["crashed", "worker exited with code -9"]),
    (WorkerRpcError("internal error", code=INTERNAL_ERROR, data={"traceback": "Traceback ...\nKeyError: 'x'"}),
     "transport", [f"JSON-RPC {INTERNAL_ERROR}", "KeyError: 'x'"]),
])
def test_worker_failures_become_edge_check_errors(tmp_path, raised, cause, fragments):
    checker = WorkerEdgeChecker(PoolDouble(raised), edge_plan(tmp_path))

    with pytest.raises(EdgeCheckError) as caught:
        checker.check(edge_call(), cassette=CassetteConfig(), workspace="/ws", timeout_s=2.5, on_event=print)

    assert caught.value.cause == cause
    assert all(fragment in caught.value.message for fragment in fragments)


def test_a_branch_the_plan_assigns_no_venv_is_a_config_error(tmp_path):
    pool = PoolDouble(RESULT_JSON)
    checker = WorkerEdgeChecker(pool, edge_plan(tmp_path, keys=("q:validate.done[save]",)))

    with pytest.raises(EdgeCheckError) as caught:
        checker.check(edge_call(), cassette=CassetteConfig(), workspace="/ws", timeout_s=None, on_event=print)

    assert (caught.value.cause, pool.calls) == ("config", [])
    assert "p:validate.done[save]" in caught.value.message


# --- real workers (sys.executable) with the fake provider ----------------------------------------------------------

def worker_env(script: Any = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != SCRIPT_VAR}
    if script is not None:
        env[SCRIPT_VAR] = script if isinstance(script, str) else json.dumps(script)
    return env


def fake_call(**kwargs: Any) -> EdgeCheckCall:
    return edge_call(provider="fake", model_id="fake", **kwargs)


def test_a_check_runs_in_the_worker_of_its_venv(tmp_path):
    script = [{"match": {"input": {"transition": {"to": "save"}}},
               "output": {"take": False, "reason": "The document says PRO FORMA."}}]
    plan = edge_plan(tmp_path)
    events: list[dict[str, Any]] = []

    with WorkerPool(plan, env=worker_env(script)) as pool:
        result = WorkerEdgeChecker(pool, plan).check(fake_call(), cassette=CassetteConfig(),
                                                     workspace=str(tmp_path), timeout_s=60, on_event=events.append)

    assert (result.verdict, result.provider, result.model_id, result.tier) == (
        EdgeVerdict(take=False, reason="The document says PRO FORMA."), "fake", "fake", "cheap")
    assert (result.attempts, result.usage, result.replayed) == (1, Usage(cost_usd=0.0, calls=1), False)
    start, model_call = events
    assert (start["type"], start["venv"]) == ("worker.start", "edges")
    assert start["pid"] != os.getpid()
    assert (model_call["type"], model_call["step"], model_call["provider"], model_call["kind"],
            model_call["outcome"]) == ("model.call", "edge:validate.done[save]", "fake", "agent", "valid")


def test_a_failed_check_in_the_worker_keeps_its_cause(tmp_path):
    script = [{"match": {"input": {"bindings": {"amount": 1}}}, "output": {"take": "no"}}]
    plan = edge_plan(tmp_path)

    with WorkerPool(plan, env=worker_env(script)) as pool:
        checker = WorkerEdgeChecker(pool, plan)
        errors = []
        for amount in (1, 2):
            with pytest.raises(EdgeCheckError) as caught:
                checker.check(fake_call(retries=0, bindings={"amount": amount}), cassette=CassetteConfig(),
                              workspace=str(tmp_path), timeout_s=60, on_event=lambda e: None)
            errors.append(caught.value)

    invalid, unscripted = errors
    assert invalid.cause == "validation"
    assert "output.take: Input should be a valid boolean" in invalid.message
    assert unscripted.cause == "model"
    assert unscripted.message.startswith("fake provider: no scripted response")


def test_a_check_that_outlives_its_timeout_kills_the_worker(tmp_path):
    fifo = tmp_path / "script.fifo"
    os.mkfifo(fifo)                                  # the fake provider blocks reading its script
    plan = edge_plan(tmp_path)

    with WorkerPool(plan, env=worker_env(str(fifo))) as pool:
        pool.start()
        [before] = pool.status()
        t0 = time.monotonic()
        with pytest.raises(EdgeCheckError) as caught:
            WorkerEdgeChecker(pool, plan).check(fake_call(), cassette=CassetteConfig(), workspace=str(tmp_path),
                                                timeout_s=1.0, on_event=lambda e: None)
        elapsed = time.monotonic() - t0
        [after] = pool.status()

    assert (caught.value.cause, caught.value.message) == ("timeout", "no verdict within 1s")
    assert 1.0 <= elapsed < 10
    assert before["alive"] and not after["alive"]


def test_worker_cassettes_record_then_replay_without_the_provider(tmp_path):
    script = [{"output": {"take": True, "reason": "It requests payment."}}]
    staging = tmp_path / "staging"
    plan = edge_plan(tmp_path)

    def run(env: dict[str, str], call: EdgeCheckCall, cassette: CassetteConfig, workspace: Path) -> EdgeCheckResult:
        workspace.mkdir()
        with WorkerPool(plan, env=env) as pool:
            return WorkerEdgeChecker(pool, plan).check(call, cassette=cassette, workspace=str(workspace),
                                                       timeout_s=60, on_event=lambda e: None)

    recorded = run(worker_env(script), fake_call(), CassetteConfig(mode="record", record_dir=str(staging)),
                   tmp_path / "ws1")
    replayed = run(worker_env(), fake_call(run_id="run-2"), CassetteConfig(mode="replay", dir=str(staging)),
                   tmp_path / "ws2")
    with pytest.raises(EdgeCheckError) as caught:
        run(worker_env(), fake_call(check="The document is a quote."), CassetteConfig(mode="replay", dir=str(staging)),
            tmp_path / "ws3")

    assert len(list(staging.glob("*.json"))) == 1
    assert replayed.verdict == recorded.verdict == EdgeVerdict(take=True, reason="It requests payment.")
    assert (recorded.replayed, replayed.replayed) == (False, True)
    assert caught.value.cause == "cassette_miss"
    assert caught.value.message.startswith(NO_RECORDING)


@pytest.mark.live
def test_claude_code_checks_an_edge_in_a_worker_live(tmp_path):
    """The default provider through a real worker, recording: no tools, the verdict envelope, one cassette."""
    plan = edge_plan(tmp_path)
    staging, workspace = tmp_path / "staging", tmp_path / "ws"
    workspace.mkdir()
    events: list[dict[str, Any]] = []

    with WorkerPool(plan, env=dict(os.environ)) as pool:
        result = WorkerEdgeChecker(pool, plan).check(
            edge_call(provider="claude-code", model_id="haiku"),
            cassette=CassetteConfig(mode="record", record_dir=str(staging)), workspace=str(workspace), timeout_s=180,
            on_event=events.append)

    assert result.verdict.take is False              # the context is a PRO FORMA; the check asks for a final invoice
    assert result.verdict.reason
    assert (result.provider, result.tier, result.replayed) == ("claude-code", "cheap", False)
    assert result.usage.calls >= 1
    assert len(list(staging.glob("*.json"))) == result.attempts
    assert [(e["step"], e["provider"]) for e in events if e["type"] == "model.call"][-1] == (
        "edge:validate.done[save]", "claude-code")

"""The middleware chain (SPEC §3.6; PLAN §3.9 non-agentic rows, §5.2) and `build_policy` (PLAN §5.1)."""

import os
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

from support.rt_step_doubles import chain, make_params, use_agentic_checks, use_providers
from wynd.runtime.errors import StepFailure
from wynd.runtime.handle import StepCache
from wynd.runtime.middleware import AgentCall, AgentResult
from wynd.runtime.policy import CassetteConfig, build_policy
from wynd.runtime.step import AgenticStep, DeterministicStep
from wynd.runtime.storage.local import FileRegistry
from wynd.runtime.usage import ModelInfo, Usage
from wynd.runtime.worker.protocol import RunStepResult
from wynd.spec.hashing import hash_obj
from wynd.spec.lockfiles import McpSnapshot, McpToolSnapshot, RetryPolicy, StepLock
from wynd.spec.process_doc import RetryOverride
from wynd.spec.records import StepError


class In(BaseModel):
    n: int


class Done(BaseModel):
    exit: Literal["done"] = "done"
    doubled: int


class Odd(BaseModel):
    exit: Literal["odd"] = "odd"
    n: int


class Double(DeterministicStep):
    Input = In
    Output = Done | Odd

    def run(self, input):
        if input.n % 2:
            return Odd(n=input.n)
        return Done(doubled=input.n * 2)


def _probe_class(**behaviour):
    """A step recording its calls in `calls`; `behaviour` maps pre/run/post to a callable(step, input)."""
    calls: list = []

    class Probe(DeterministicStep):
        Input = In
        Output = Done

        def pre(self):
            calls.append(("pre", id(self)))
            behaviour.get("pre", lambda s: None)(self)

        def run(self, input):
            calls.append(("run", id(self)))
            return behaviour.get("run", lambda s, i: Done(doubled=i.n * 2))(self, input)

        def post(self):
            calls.append(("post", id(self)))
            behaviour.get("post", lambda s: None)(self)

    return Probe, calls


def _raise(exc):
    def fail(*args):
        raise exc

    return fail


# --- success and error exits --------------------------------------------------------------------------------------

def test_success_returns_the_exit_outputs_and_summary(tmp_path):
    result = chain(Double, make_params(tmp_path, {"n": 4}, step_path="calc"))
    assert (result.exit, result.outputs, result.attempts) == ("done", {"doubled": 8}, 1)
    assert result.output == Done(doubled=8)
    assert result.summary.model_dump() == {"step": "calc", "exit": "done", "key_outputs": {"doubled": 8}, "note": ""}
    assert (result.usage, result.model, result.validation_failures, result.replayed) == (None, None, 0, False)
    timings = result.timings
    assert timings.worker_ms >= 0 and timings.pre_ms is not None and timings.run_ms is not None
    assert timings.post_ms is not None and timings.ended_at >= timings.started_at
    wire = RunStepResult.model_validate_json(result.to_result().model_dump_json())
    assert (wire.exit, wire.outputs, wire.attempts) == ("done", {"doubled": 8}, 1)


def test_second_declared_exit(tmp_path):
    result = chain(Double, make_params(tmp_path, {"n": 3}))
    assert (result.exit, result.outputs) == ("odd", {"n": 3})


def test_input_validation_failure_keeps_the_raw_inputs(tmp_path):
    probe, calls = _probe_class()
    result = chain(probe, make_params(tmp_path, {"n": "many", "extra": [1]}))
    assert result.exit == "error"
    assert isinstance(result.output, StepError)
    assert (result.outputs["cause"], result.outputs["attempts"]) == ("input_validation", 0)
    assert result.outputs["inputs"] == {"n": "many", "extra": [1]}
    assert result.outputs["type"] == "ValidationError"
    assert calls == []
    assert set(result.summary.key_outputs) == {"cause", "message", "type", "attempts"}


def test_run_exception_has_type_traceback_inputs_and_post_still_runs(tmp_path):
    probe, calls = _probe_class(run=_raise(ValueError("no total found")))
    result = chain(probe, make_params(tmp_path, {"n": 2}))
    out = result.outputs
    assert (result.exit, out["cause"], out["type"], out["attempts"]) == ("error", "exception", "ValueError", 1)
    assert out["message"] == "ValueError: no total found"
    assert "Traceback (most recent call last)" in out["traceback"] and "no total found" in out["traceback"]
    assert out["inputs"] == {"n": 2}
    assert [name for name, _ in calls] == ["pre", "run", "post"]


def test_pre_failure_is_a_hook_error_and_skips_run_and_post(tmp_path):
    probe, calls = _probe_class(pre=_raise(RuntimeError("no db")))
    result = chain(probe, make_params(tmp_path, {"n": 2}))
    assert (result.exit, result.outputs["cause"]) == ("error", "hook")
    assert [name for name, _ in calls] == ["pre"]


def test_post_failure_after_run_keeps_the_run_output_as_partial(tmp_path):
    probe, _ = _probe_class(post=_raise(OSError("flush failed")))
    result = chain(probe, make_params(tmp_path, {"n": 5}))
    assert result.outputs["cause"] == "hook"
    assert result.outputs["partial_outputs"] == {"exit": "done", "doubled": 10}


def test_system_exit_in_run_is_an_exception(tmp_path):
    probe, _ = _probe_class(run=_raise(SystemExit(3)))
    result = chain(probe, make_params(tmp_path, {"n": 1}))
    assert (result.outputs["cause"], result.outputs["type"]) == ("exception", "SystemExit")


@pytest.mark.parametrize(("returned", "partial"), [
    ({"doubled": "lots"}, {"doubled": "lots"}),
    (42, {"repr": "42"}),
])
def test_invalid_output_is_output_validation_without_retry(tmp_path, returned, partial):
    probe, calls = _probe_class(run=lambda s, i: returned)
    retries = RetryPolicy(run=2, validation=2)
    result = chain(probe, make_params(tmp_path, {"n": 1}, retries=retries))
    assert (result.outputs["cause"], result.outputs["attempts"]) == ("output_validation", 1)
    assert result.outputs["partial_outputs"] == partial
    assert [name for name, _ in calls].count("run") == 1


def test_bad_output_declaration_is_an_import_error(tmp_path):
    class Broken(DeterministicStep):
        Input = In
        Output = Odd          # a plain model must be the done exit

        def run(self, input):
            return Odd(n=1)

    result = chain(Broken, make_params(tmp_path, {"n": 1}))
    assert (result.outputs["cause"], result.outputs["attempts"]) == ("import", 0)
    assert "Broken" in result.outputs["message"]


# --- retries (PLAN §3.9: `run` for non-agentic steps only) ----------------------------------------------------------

def test_run_retries_use_a_fresh_instance_each_time(tmp_path):
    probe, calls = _probe_class(run=_raise(ConnectionError("flaky")))
    result = chain(probe, make_params(tmp_path, {"n": 1}, retries=RetryPolicy(run=2)))
    assert (result.outputs["cause"], result.outputs["attempts"]) == ("exception", 3)
    instances = {instance for name, instance in calls if name == "run"}
    assert len([c for c in calls if c[0] == "run"]) == 3 and len(instances) == 3


def test_run_retry_succeeds_on_a_later_attempt(tmp_path):
    seen: list = []

    def flaky(step, input):
        seen.append(1)
        if len(seen) < 3:
            raise TimeoutError("slow")
        return Done(doubled=input.n * 2)

    probe, _ = _probe_class(run=flaky)
    result = chain(probe, make_params(tmp_path, {"n": 2}, retries=RetryPolicy(run=2)))
    assert (result.exit, result.attempts, result.outputs) == ("done", 3, {"doubled": 4})


def test_hook_failures_are_retried(tmp_path):
    seen: list = []

    def pre(step):
        seen.append(1)
        if len(seen) == 1:
            raise RuntimeError("warming up")

    probe, _ = _probe_class(pre=pre)
    result = chain(probe, make_params(tmp_path, {"n": 2}, retries=RetryPolicy(run=1)))
    assert (result.exit, result.attempts) == ("done", 2)


def test_config_failures_are_never_retried(tmp_path):
    probe, calls = _probe_class(run=_raise(StepFailure("config", "no key")))
    result = chain(probe, make_params(tmp_path, {"n": 1}, retries=RetryPolicy(run=3)))
    assert (result.outputs["cause"], result.outputs["message"], result.outputs["type"]) == ("config", "no key", None)
    assert [name for name, _ in calls].count("run") == 1


# --- process state isolation --------------------------------------------------------------------------------------

def test_env_and_cwd_are_restored_and_cwd_is_the_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("WYND_T_CHANGE", "before")
    monkeypatch.setenv("WYND_T_REMOVE", "keep me")
    monkeypatch.delenv("WYND_T_ADD", raising=False)
    workspace = tmp_path / "ws"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    seen = {}

    def pre(step):
        os.environ["WYND_T_ADD"] = "added"
        os.environ["WYND_T_CHANGE"] = "after"

    def run(step, input):
        seen["cwd"] = Path.cwd().resolve()
        del os.environ["WYND_T_REMOVE"]
        os.chdir(elsewhere)
        return Done(doubled=0)

    probe, _ = _probe_class(pre=pre, run=run)
    before = Path.cwd()
    result = chain(probe, make_params(workspace, {"n": 0}))
    assert result.exit == "done"
    assert seen["cwd"] == workspace.resolve()
    assert Path.cwd() == before
    assert "WYND_T_ADD" not in os.environ
    assert os.environ["WYND_T_CHANGE"] == "before"
    assert os.environ["WYND_T_REMOVE"] == "keep me"


def test_env_is_restored_after_a_failed_attempt(tmp_path, monkeypatch):
    monkeypatch.delenv("WYND_T_LEAK", raising=False)

    def run(step, input):
        os.environ["WYND_T_LEAK"] = "1"
        raise ValueError("boom")

    probe, _ = _probe_class(run=run)
    chain(probe, make_params(tmp_path, {"n": 0}))
    assert "WYND_T_LEAK" not in os.environ


# --- the runtime handle inside the chain ---------------------------------------------------------------------------

def test_runtime_handle_logger_trace_and_cache(tmp_path):
    seen = {}

    class Uses(DeterministicStep):
        Input = In
        Output = Done

        def run(self, input):
            rt = self.runtime
            seen.update(run_id=rt.run_id, path=rt.step_path, run=rt.step_run, ws=rt.workspace, effects=rt.effects)
            rt.logger.info("working on %s", input.n)
            rt.log("done", n=input.n)
            rt.trace.event("progress", pct=50)
            count = rt.cache.get("count", 0) + 1
            rt.cache.set("count", count)
            return Done(doubled=count)

    events: list = []
    cache = StepCache()
    params = make_params(tmp_path, {"n": 7}, step_path="sub.uses", effects=("filesystem",))
    first = chain(Uses, params, events, cache)
    second = chain(Uses, params, [], cache)
    third = chain(Uses, params, [], StepCache())
    assert seen == {"run_id": "run_test", "path": "sub.uses", "run": 1, "ws": tmp_path, "effects": ("filesystem",)}
    assert [r.outputs["doubled"] for r in (first, second, third)] == [1, 2, 1]
    assert events == [
        {"type": "step.log", "level": "INFO", "stream": "logger", "message": "working on 7"},
        {"type": "step.log", "level": "INFO", "stream": "logger", "message": 'done {"n": 7}'},
        {"type": "step.event", "name": "progress", "data": {"pct": 50}},
    ]


def test_missing_env_var_is_a_config_error(tmp_path, monkeypatch):
    monkeypatch.delenv("WYND_T_TOKEN", raising=False)

    class NeedsToken(DeterministicStep):
        Input = In
        Output = Done

        def run(self, input):
            self.runtime.env("WYND_T_TOKEN")
            return Done(doubled=0)

    result = chain(NeedsToken, make_params(tmp_path, {"n": 0}))
    assert (result.outputs["cause"], result.outputs["type"]) == ("config", "MissingEnvVar")
    assert "WYND_T_TOKEN is not set" in result.outputs["message"]


# --- the agentic seam (monkeypatched `complete`) --------------------------------------------------------------------

@pytest.fixture
def agentic(monkeypatch):
    use_agentic_checks(monkeypatch)

    class Extract(AgenticStep):
        """Extract the number."""

        context = ["process.goal"]
        Input = In
        Output = Done | Odd

        def run(self, input): ...

    return Extract


def test_agentic_step_goes_through_complete(tmp_path, monkeypatch, agentic):
    calls: list = []
    usage = Usage(input_tokens=10, output_tokens=5, cost_usd=0.01, latency_ms=12.0, calls=2)
    model = ModelInfo(provider="fake", model_id="fake-1", tier="cheap", thinking="low")

    def complete(step, call):
        calls.append((step, call))
        call.runtime.trace.emit("model.call", provider="fake")
        return AgentResult(output=Odd(n=call.input.n), note="it was odd", usage=usage, attempts=2, model=model)

    monkeypatch.setattr("wynd.runtime.agentic.loop.complete", complete)
    cassette = CassetteConfig(mode="replay", dir=str(tmp_path / "cassettes"))
    events: list = []
    params = make_params(tmp_path / "ws", {"n": 9}, kind="agentic", context={"process.goal": "count"},
                         cassette=cassette, retries=RetryPolicy(run=2, validation=2, tool=1), provider="fake",
                         model_id="fake-1", tier="cheap", thinking="low", step_path="extract")
    result = chain(agentic, params, events)

    [(step, call)] = calls
    assert isinstance(step, agentic) and isinstance(call, AgentCall)
    assert call.step_path == "extract" and call.input == In(n=9)
    assert call.context == {"process.goal": "count"}
    assert call.policy == params.policy and call.cassette == cassette
    assert list(call.interface.exits) == ["done", "odd"]
    assert call.runtime is step.runtime and call.runtime.workspace == tmp_path / "ws"
    assert (result.exit, result.outputs, result.attempts) == ("odd", {"n": 9}, 2)
    assert (result.usage, result.model, result.validation_failures, result.replayed) == (usage, model, 1, True)
    assert result.summary.note == "it was odd"
    assert events == [{"type": "model.call", "provider": "fake"}]


def test_agentic_failure_keeps_usage_and_is_not_restarted_by_the_chain(tmp_path, monkeypatch, agentic):
    calls: list = []
    usage = Usage(input_tokens=3, calls=1)

    def complete(step, call):
        calls.append(call)
        raise StepFailure("tool", "tool lookup failed: KeyError", usage=usage, attempts=1)

    monkeypatch.setattr("wynd.runtime.agentic.loop.complete", complete)
    params = make_params(tmp_path, {"n": 1}, kind="agentic", retries=RetryPolicy(run=2, validation=2, tool=1))
    result = chain(agentic, params)
    assert (result.exit, result.outputs["cause"], result.outputs["message"]) == (
        "error", "tool", "tool lookup failed: KeyError")
    assert (result.usage, result.outputs["attempts"], len(calls)) == (usage, 1, 1)
    assert (result.validation_failures, result.replayed) == (0, False)


def test_exhausted_validation_retries_count_every_attempt(tmp_path, monkeypatch, agentic):
    def complete(step, call):
        raise StepFailure("output_validation", "total: field required", attempts=3, usage=Usage(calls=3))

    monkeypatch.setattr("wynd.runtime.agentic.loop.complete", complete)
    params = make_params(tmp_path, {"n": 1}, kind="agentic", cassette=CassetteConfig(mode="replay"))
    result = chain(agentic, params)
    wire = result.to_result()
    assert (wire.exit, wire.attempts, wire.validation_failures, wire.replayed) == ("error", 3, 3, True)
    assert result.outputs["cause"] == "output_validation" and wire.usage == Usage(calls=3)


def test_agentic_output_is_revalidated(tmp_path, monkeypatch, agentic):
    usage = Usage(calls=1)
    model = ModelInfo(provider="fake", model_id="fake")

    class Stray(BaseModel):
        exit: Literal["stray"] = "stray"

    monkeypatch.setattr("wynd.runtime.agentic.loop.complete",
                        lambda step, call: AgentResult(Stray(), "", usage, 3, model))
    result = chain(agentic, make_params(tmp_path, {"n": 1}, kind="agentic"))
    assert (result.outputs["cause"], result.outputs["attempts"], result.usage) == ("output_validation", 3, usage)


# --- build_policy ---------------------------------------------------------------------------------------------------

def _lock(**fields) -> StepLock:
    return StepLock.model_validate({"name": "s", "entrypoint": "s:S", **fields})


def _snapshot(server: str) -> McpSnapshot:
    tools = [McpToolSnapshot(name="get_issue", input_schema={"type": "object"})]
    return McpSnapshot(server=server, allow=["get_issue"], tools=tools,
                       hash=hash_obj([t.model_dump(mode="json") for t in tools]))


def test_policy_for_non_agentic_steps(tmp_path):
    registry = FileRegistry(tmp_path)
    plain = build_policy("deterministic", None, default_provider="fake", registry=registry, environ={})
    assert (plain.kind, plain.retries, plain.provider, plain.effects) == ("deterministic", RetryPolicy(), None, [])
    lock = _lock(kind="shell", retries={"run": 1}, effects=["filesystem", "shell"])
    shell = build_policy("shell", lock, default_provider="fake", registry=registry, environ={},
                         retry_override=RetryOverride(run=4))
    assert (shell.retries, shell.effects, shell.model_id) == (RetryPolicy(run=4), ["filesystem", "shell"], None)


def test_policy_for_agentic_steps(tmp_path, monkeypatch):
    use_providers(monkeypatch)
    registry = FileRegistry(tmp_path)
    default = build_policy("agentic", None, default_provider="claude-code", registry=registry, environ={})
    assert (default.provider, default.tier, default.thinking, default.model_id) == (
        "claude-code", "cheap", "low", "haiku")
    assert (default.retries, default.max_turns, default.builtin_tools) == (
        RetryPolicy(run=2, validation=2, tool=1), 25, [])
    lock = _lock(kind="agentic", provider="fake", tier="standard", thinking="high", builtin_tools=["Read"],
                 max_turns=5, retries={"run": 0, "validation": 1, "tool": 0}, effects=["network"])
    custom = build_policy("agentic", lock, default_provider="claude-code", registry=registry, environ={},
                          retry_override=RetryOverride(validation=4))
    assert (custom.provider, custom.tier, custom.thinking, custom.model_id) == ("fake", "standard", "high", "fake")
    assert (custom.builtin_tools, custom.max_turns, custom.effects) == (["Read"], 5, ["network"])
    assert custom.retries == RetryPolicy(run=0, validation=4, tool=0)


def test_policy_unknown_provider_is_a_config_failure(tmp_path, monkeypatch):
    use_providers(monkeypatch)
    with pytest.raises(StepFailure) as err:
        build_policy("agentic", _lock(kind="agentic", provider="nope"), default_provider="fake",
                     registry=FileRegistry(tmp_path), environ={})
    assert err.value.cause == "config"


def test_policy_resolves_mcp_entries_with_url_override(tmp_path, monkeypatch):
    use_providers(monkeypatch)
    registry = FileRegistry(tmp_path)
    registry.put("mcp", "git-hub", {"transport": "http", "url": "https://example.test/mcp",
                                    "headers": {"Authorization": "Bearer ${env:GH_TOKEN}"}, "auth_env": ["GH_TOKEN"]})
    registry.put("mcp", "files", {"transport": "stdio", "command": ["fs-server"]})
    lock = _lock(kind="agentic", effects=["network"], mcp=[_snapshot("git-hub"), _snapshot("files")])
    environ = {"WYND_MCP_GIT_HUB_URL": "http://mcp.cluster.local/", "WYND_MCP_FILES_URL": "http://ignored/"}
    policy = build_policy("agentic", lock, default_provider="fake", registry=registry, environ=environ)
    github, files = policy.mcp
    assert github["server"] == "git-hub" and github["allow"] == ["get_issue"] and github["hash"].startswith("sha256:")
    assert github["entry"]["name"] == "git-hub" and github["entry"]["url"] == "http://mcp.cluster.local/"
    assert github["entry"]["auth_env"] == ["GH_TOKEN"]
    assert files["entry"]["transport"] == "stdio" and files["entry"]["url"] is None
    unchanged = build_policy("agentic", lock, default_provider="fake", registry=registry, environ={})
    assert unchanged.mcp[0]["entry"]["url"] == "https://example.test/mcp"


def test_policy_missing_or_invalid_mcp_entry_is_a_config_failure(tmp_path, monkeypatch):
    use_providers(monkeypatch)
    registry = FileRegistry(tmp_path)
    lock = _lock(kind="agentic", effects=["network"], mcp=[_snapshot("github")])
    with pytest.raises(StepFailure, match="'github' is not in the user registry") as err:
        build_policy("agentic", lock, default_provider="fake", registry=registry, environ={})
    assert err.value.cause == "config"
    registry.put("mcp", "github", {"transport": "carrier-pigeon"})
    with pytest.raises(StepFailure, match="invalid registry entry") as err:
        build_policy("agentic", lock, default_provider="fake", registry=registry, environ={})
    assert err.value.cause == "config"

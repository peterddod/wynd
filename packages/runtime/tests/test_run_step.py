"""Step-test helpers (PLAN §3.20): `load_step`, `run_step`, `expect`, `match_outputs`, `sub_tmp`."""

import json
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

from support.rt_step_doubles import FIXTURES, use_agentic_checks, use_loader, use_providers
from wynd.runtime.cassettes import CassetteMissError
from wynd.runtime.errors import StepFailure
from wynd.runtime.middleware import AgentResult
from wynd.runtime.step import AgenticStep, DeterministicStep, ProcessStep
from wynd.runtime.testing import Mismatch, StepResult, expect, load_step, match_outputs, run_step, sub_tmp
from wynd.runtime.usage import ModelInfo, Usage
from wynd.spec.lockfiles import RetryPolicy
from wynd.spec.records import StepError


class Text(BaseModel):
    text: str


class Upper(BaseModel):
    exit: Literal["done"] = "done"
    text: str
    day: date


class Shout(DeterministicStep):
    Input = Text
    Output = Upper

    def run(self, input):
        self.runtime.logger.warning("shouting")
        return Upper(text=input.text.upper(), day=date(2026, 10, 1))


class Failing(DeterministicStep):
    Input = Text
    Output = Upper

    def run(self, input):
        raise NotImplementedError("deferred to the agentic half")


def test_run_step_returns_a_typed_result(tmp_path):
    result = run_step(Shout, {"text": "hi"}, workspace=tmp_path / "ws")
    assert isinstance(result, StepResult)
    assert (result.exit, result.output, result.attempts) == ("done", Upper(text="HI", day=date(2026, 10, 1)), 1)
    assert result.outputs == {"text": "HI", "day": "2026-10-01"}
    assert result.summary.step == "Shout" and result.workspace == tmp_path / "ws" and result.workspace.is_dir()
    assert result.events == [{"type": "step.log", "level": "WARNING", "stream": "logger", "message": "shouting"}]
    assert (result.usage, result.model) == (None, None)


def test_run_step_defaults_to_a_fresh_temp_workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    first, second = run_step(Shout, Text(text="a")), run_step(Shout, Text(text="b"))
    assert first.workspace != second.workspace
    assert first.workspace.parent == tmp_path
    assert first.workspace.name.startswith("wynd-step-") and first.workspace.is_dir()


def test_run_step_error_exit_keeps_json_inputs(tmp_path):
    result = run_step(Failing, {"text": Path("/data/in.txt")}, workspace=tmp_path)
    assert result.exit == "error" and isinstance(result.output, StepError)
    assert (result.outputs["cause"], result.outputs["type"]) == ("exception", "NotImplementedError")
    assert result.outputs["inputs"] == {"text": "/data/in.txt"}


def test_run_step_refuses_process_steps():
    class Child(ProcessStep):
        process_id = "child"

    with pytest.raises(TypeError, match="ProcessStep"):
        run_step(Child, {})


def test_events_file_gets_every_event(tmp_path, monkeypatch):
    events_file = tmp_path / "events.jsonl"
    monkeypatch.setenv("WYND_EVENTS_FILE", str(events_file))
    run_step(Shout, {"text": "a"}, workspace=tmp_path / "ws")
    run_step(Shout, {"text": "b"}, workspace=tmp_path / "ws")
    lines = [json.loads(line) for line in events_file.read_text().splitlines()]
    assert [line["message"] for line in lines] == ["shouting", "shouting"]


# --- load_step (the worker's loader is doubled: RT-WORKER, wave 2b) -------------------------------------------------

def test_load_step_resolves_the_entrypoint_from_the_lock(monkeypatch, tmp_path):
    use_loader(monkeypatch)
    Echo = load_step(FIXTURES / "echo")
    assert Echo.__name__ == "Echo" and Echo.__module__.startswith("wynd_steps.echo_")
    assert load_step(FIXTURES / "echo") is Echo
    result = run_step(Echo, {"text": "hey"}, workspace=tmp_path)
    assert (result.exit, result.outputs) == ("done", {"text": "HEY", "length": 3})
    assert run_step(Echo, {"text": ""}, workspace=tmp_path).exit == "empty"


def test_load_step_with_an_explicit_class_needs_no_lock(monkeypatch, tmp_path):
    use_loader(monkeypatch)
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "probe.py").write_text(PROBE)
    Probe = load_step(package, "probe:Probe")
    assert Probe.__name__ == "Probe"
    with pytest.raises(FileNotFoundError):
        load_step(tmp_path / "pkg")


PROBE = '''
from typing import Literal
from pydantic import BaseModel
from wynd.runtime import DeterministicStep


class Probe(DeterministicStep):
    class Input(BaseModel):
        n: int

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        effects: list[str]

    def run(self, input):
        return self.Output(effects=list(self.runtime.effects))
'''


def test_run_step_reads_the_package_lock_by_default(monkeypatch, tmp_path):
    use_loader(monkeypatch)
    package = tmp_path / "probe"
    package.mkdir()
    (package / "probe.py").write_text(PROBE)
    (package / "step.lock.yaml").write_text(
        "wynd: 1\nname: probe\nkind: deterministic\nentrypoint: probe:Probe\neffects: [network, filesystem]\n"
    )
    Probe = load_step(package)
    assert run_step(Probe, {"n": 1}, workspace=tmp_path / "ws").outputs == {"effects": ["network", "filesystem"]}
    override = {"name": "probe", "kind": "deterministic", "entrypoint": "probe:Probe", "effects": ["shell"]}
    assert run_step(Probe, {"n": 1}, lock=override, workspace=tmp_path / "ws").outputs == {"effects": ["shell"]}


# --- the agentic path (monkeypatched `complete`, doubled tier resolution) -----------------------------------------------

class Done(BaseModel):
    exit: Literal["done"] = "done"
    total: float


@pytest.fixture
def agentic(monkeypatch):
    use_agentic_checks(monkeypatch)
    use_providers(monkeypatch)
    calls: list = []
    reply: dict = {"output": Done(total=12.5)}

    def complete(step, call):
        calls.append(call)
        if "failure" in reply:
            raise reply["failure"]
        return AgentResult(reply["output"], "", Usage(calls=1), 1, ModelInfo(provider=call.policy.provider,
                                                                             model_id=call.policy.model_id))

    monkeypatch.setattr("wynd.runtime.agentic.loop.complete", complete)

    class Extract(AgenticStep):
        """Extract the total."""

        Input = Text
        Output = Done

        def run(self, input): ...

    return Extract, calls, reply


def test_agentic_run_step_replays_by_default(tmp_path, agentic):
    Extract, calls, _ = agentic
    result = run_step(Extract, {"text": "Total 12.50"}, workspace=tmp_path, context={"process.goal": "pay"})
    [call] = calls
    assert result.outputs == {"total": 12.5}
    assert call.cassette.mode == "replay"
    assert call.cassette.dir == str(Path(__file__).parent / "cassettes")
    assert call.cassette.record_dir is None
    assert call.cassette.literals == {str(tmp_path): "<workspace>"}
    assert call.context == {"process.goal": "pay"}
    assert (call.policy.provider, call.policy.tier, call.policy.model_id) == ("claude-code", "cheap", "haiku")
    assert call.policy.retries == RetryPolicy(run=2, validation=2, tool=1)
    assert result.model == ModelInfo(provider="claude-code", model_id="haiku")


def test_agentic_run_step_environment(tmp_path, monkeypatch, agentic):
    Extract, calls, _ = agentic
    monkeypatch.setenv("WYND_CASSETTE_MODE", "record")
    monkeypatch.setenv("WYND_CASSETTE_RECORD_DIR", str(tmp_path / "staging"))
    monkeypatch.setenv("WYND_CASSETTE_LITERALS", json.dumps({"/checkout/examples": "<process>"}))
    monkeypatch.setenv("WYND_DEFAULT_PROVIDER", "fake")
    run_step(Extract, {"text": "x"}, workspace=tmp_path / "ws", cassettes=tmp_path / "cas",
             retry=RetryPolicy(run=0, validation=5, tool=0))
    call = calls[-1]
    assert (call.cassette.mode, call.cassette.dir, call.cassette.record_dir) == (
        "record", str(tmp_path / "cas"), str(tmp_path / "staging"))
    assert call.cassette.literals == {"/checkout/examples": "<process>", str(tmp_path / "ws"): "<workspace>"}
    assert (call.policy.provider, call.policy.model_id) == ("fake", "fake")
    assert call.policy.retries == RetryPolicy(run=0, validation=5, tool=0)
    monkeypatch.delenv("WYND_CASSETTE_RECORD_DIR")
    run_step(Extract, {"text": "x"}, workspace=tmp_path / "ws", cassettes=tmp_path / "cas")
    assert calls[-1].cassette.record_dir == str(tmp_path / "cas")
    run_step(Extract, {"text": "x"}, mode="live", workspace=tmp_path / "ws")
    assert calls[-1].cassette.mode == "live"


def test_cassette_miss_is_re_raised(tmp_path, agentic):
    Extract, _, reply = agentic
    text = "no recording for this request — re-record with `wynd test --live`\n  key: abc\n  cassettes: /c"
    reply["failure"] = StepFailure("cassette_miss", text)
    with pytest.raises(CassetteMissError) as err:
        run_step(Extract, {"text": "x"}, workspace=tmp_path)
    assert str(err.value) == text


def test_other_agentic_failures_are_error_results(tmp_path, agentic):
    Extract, _, reply = agentic
    reply["failure"] = StepFailure("model", "refused")
    result = run_step(Extract, {"text": "x"}, workspace=tmp_path)
    assert (result.exit, result.outputs["cause"]) == ("error", "model")


# --- expect / match_outputs / sub_tmp --------------------------------------------------------------------------------

def _result(exit: str, outputs: dict) -> StepResult:
    return StepResult(exit, StepError(cause="exception", message="m"), outputs, None, 1, None, None, [], Path("."))


def test_expect_passes_on_exit_and_subset():
    expect(_result("done", {"a": 1, "b": "x"}), exit="done", outputs={"a": 1.0})
    expect(_result("done", {"a": 1}), exit="done")
    expect(_result("done", {"text": "t"}), exit="done", present=["text"])


def _payload(err: pytest.ExceptionInfo) -> dict:
    first = str(err.value).splitlines()[0]
    assert first.startswith("WYND-EXPECT ")
    return json.loads(first.removeprefix("WYND-EXPECT "))


def test_expect_reports_a_wrong_exit_with_the_error_payload():
    error = {"cause": "exception", "message": "NotImplementedError: later", "type": "NotImplementedError"}
    with pytest.raises(AssertionError) as err:
        expect(_result("error", error), exit="done", outputs={"a": 1})
    assert _payload(err) == {
        "expected_exit": "done", "actual_exit": "error", "mismatches": [],
        "error": {"cause": "exception", "message": "NotImplementedError: later", "error_type": "NotImplementedError"},
    }
    assert "NotImplementedError: later" in str(err.value)


def test_expect_reports_output_mismatches():
    with pytest.raises(AssertionError) as err:
        expect(_result("done", {"a": 2, "b": ""}), exit="done", outputs={"a": 1}, present=["b"])
    assert _payload(err)["mismatches"] == [
        {"field": "a", "expected": 1, "actual": 2}, {"field": "b", "expected": "<present>", "actual": ""},
    ]
    assert _payload(err)["error"] is None


@pytest.mark.parametrize(("expected", "actual"), [
    ({"a": 1}, {"a": 1, "b": 2}),
    ({"record": {}}, {"record": {"x": 1}}),
    ({"record": {"total": 1200.5}}, {"record": {"total": 1200.50, "k": "v"}}),
    ({"n": 1200}, {"n": 1200.0}),
    ({"n": 0.1 + 0.2}, {"n": 0.3}),
    ({"items": [{"a": 1}, 2]}, {"items": [{"a": 1, "b": 0}, 2]}),
    ({"ok": True}, {"ok": True}),
    ({"due": date(2026, 10, 1)}, {"due": "2026-10-01"}),
    ({"at": datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)}, {"at": "2026-10-01T09:30:00Z"}),
    ({"gone": None}, {}),
    ({"gone": None}, {"gone": None}),
])
def test_match_outputs_accepts(expected, actual):
    assert match_outputs(expected, actual) == []


@pytest.mark.parametrize(("expected", "actual", "mismatch"), [
    ({"a": 1}, {}, Mismatch("a", 1, None)),
    ({"a": "x"}, {"a": "X"}, Mismatch("a", "x", "X")),
    ({"n": 1}, {"n": True}, Mismatch("n", 1, True)),
    ({"ok": True}, {"ok": 1}, Mismatch("ok", True, 1)),
    ({"n": 1.0}, {"n": 1.0001}, Mismatch("n", 1.0, 1.0001)),
    ({"n": 1}, {"n": "1"}, Mismatch("n", 1, "1")),
    ({"items": [1, 2]}, {"items": [1, 2, 3]}, Mismatch("items", [1, 2], [1, 2, 3])),
    ({"items": [1, {"k": 2}]}, {"items": [1, {"k": 3}]}, Mismatch("items[1].k", 2, 3)),
    ({"r": {"k": 1}}, {"r": "flat"}, Mismatch("r", {"k": 1}, "flat")),
    ({"gone": None}, {"gone": 0}, Mismatch("gone", None, 0)),
    ({"due": date(2026, 10, 1)}, {"due": "2026-10-02"}, Mismatch("due", "2026-10-01", "2026-10-02")),
])
def test_match_outputs_rejects(expected, actual, mismatch):
    assert match_outputs(expected, actual) == [mismatch]


def test_match_outputs_present_fields():
    assert match_outputs({}, {"text": "hello", "n": 0}, present=["text", "n"]) == []
    assert match_outputs({}, {"text": "", "none": None}, present=["text", "none", "absent"]) == [
        Mismatch("text", "<present>", ""), Mismatch("none", "<present>", None), Mismatch("absent", "<present>", None),
    ]


def test_sub_tmp_replaces_a_leading_placeholder_only(tmp_path):
    value = {"dest": "{tmp}/records", "note": "see {tmp}", "list": ["{tmp}", 3, ("{tmp}/a",)], "n": None}
    assert sub_tmp(value, tmp_path) == {
        "dest": f"{tmp_path}/records", "note": "see {tmp}", "list": [str(tmp_path), 3, (f"{tmp_path}/a",)], "n": None,
    }
    assert sub_tmp("{tmp}", "/x") == "/x"

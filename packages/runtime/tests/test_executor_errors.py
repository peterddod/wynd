"""Executor error handling (SPEC §3.5; PLAN §3.5, §3.8, §3.9; `$DRAFTS/02 §13` test_executor_errors): step error
exits, the default and custom handlers, `finally`, timeouts, worker crashes, the input boundary, run lifecycle and
workspace retention."""

from __future__ import annotations

import dataclasses
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import pytest

from wynd.runtime.errors import InvalidProcessInputs
from wynd.runtime.usage import ModelInfo, Usage
from wynd.runtime.worker.client import StepTimeout, WorkerCrashed
from wynd.spec.lockfiles import StepLock

FALLBACK = """
kind: process
name: p
entry: x
inputs: {text: string}
outputs: {text: string}
steps: {x: {use: ./steps/x}, fallback: {use: ./steps/fallback}}
edges:
  - {from: x.done, to: $exit.done, with: {text: steps.x.outputs.text}}
  - {from: x.error, to: fallback, with: {text: steps.x.outputs.inputs.text, cause: steps.x.outputs.cause}}
  - {from: fallback.done, to: $exit.done, with: {text: steps.fallback.outputs.text}}
"""


def boom(message: str = "boom"):
    def run(_):
        raise RuntimeError(message)
    return run


def test_error_exit_with_an_edge_rebinds_the_failed_inputs(graph, make_step):
    fallback = make_step(lambda i: {"text": f"{i.text} via {i.cause}"}, inputs={"text": str, "cause": str},
                         done={"text": str})
    g = graph(FALLBACK, {"x": make_step(boom(), inputs={"text": str}, done={"text": str}), "fallback": fallback})

    result = g.run({"text": "hello"})

    assert (result.exit, result.status, result.error) == ("done", "succeeded", None)
    assert result.outputs == {"text": "hello via exception"}
    x_end = g.events(result.run_id, "step.end")[0]
    assert (x_end["exit"], x_end["outputs"]["cause"], x_end["outputs"]["type"]) == (
        "error", "exception", "RuntimeError")
    assert "exit" not in x_end["outputs"]
    assert result.workspace is None and g.stores.workspaces.locate(result.run_id) is None


ONE = """
kind: process
name: p
entry: x
inputs: {text: string}
outputs: {}
steps: {x: {use: ./steps/x}}
edges:
  - {from: x.done, to: $exit.done}
"""


def test_unrouted_error_goes_to_the_default_handler_and_keeps_the_workspace(graph, make_step):
    g = graph(ONE, {"x": make_step(boom("bad input"), inputs={"text": str})})

    result = g.run({"text": "t"})

    assert (result.exit, result.status) == ("error", "failed")
    error = result.error
    assert (error.cause, error.step, error.process, error.run_id) == ("step_error", "x", "p", result.run_id)
    assert error.step_error.cause == "exception" and "bad input" in error.step_error.message
    assert error.inputs == {"text": "t"} and error.edge is None
    assert result.outputs == {"error": error.model_dump(mode="json")}
    [event] = g.events(result.run_id, "process.error")
    assert (event["handler"], event["error"]["cause"], event["parent"]) == ("default", "step_error", None)
    assert (error.trace.seq, error.trace.uri) == (event["seq"], result.trace)
    kept = g.stores.workspaces.locate(result.run_id)
    assert kept is not None and error.workspace == kept == result.workspace
    assert Path(urlparse(kept).path, ".wynd/steps/x/1/outputs.json").is_file()
    record = g.record(result.run_id)
    assert (record["status"], record["exit"], record["error"]["cause"], record["workspace"]) == (
        "failed", "error", "step_error", kept)
    end = g.events(result.run_id, "run.end")[0]
    assert (end["status"], end["workspace_kept"], end["workspace"]) == ("failed", True, kept)


HANDLED = """
kind: process
name: p
entry: x
inputs: {text: string}
outputs:
  done: {}
  parked: {reason: string}
steps: {x: {use: ./steps/x}, park: {use: ./steps/park}}
edges:
  - {from: x.done, to: $exit.done}
on_error: park
"""


def test_custom_handler_receives_the_process_error_and_maps_its_exit(graph, make_step):
    seen = []

    def park(i):
        seen.append(i.model_dump())
        return {"exit": "parked", "reason": f"{i.cause} at {i.step}"}

    handler = make_step(park, inputs={"cause": str, "step": str, "run_id": str, "message": str},
                        done={}, parked={"reason": str})
    g = graph(HANDLED, {"x": make_step(boom(), inputs={"text": str}), "park": handler})

    result = g.run({"text": "t"}, run_id="run-handled")

    assert (result.exit, result.status, result.outputs) == ("parked", "succeeded", {"reason": "step_error at x"})
    assert result.error is not None and result.error.cause == "step_error"
    assert seen == [{"cause": "step_error", "step": "x", "run_id": "run-handled",
                     "message": result.error.message}]
    starts = g.events(result.run_id, "step.start")
    assert [(s["step"], s["role"], s["via"]) for s in starts] == [("x", "node", None), ("park", "on_error", "on_error")]
    assert g.events(result.run_id, "process.error")[0]["handler"] == "park"
    assert result.workspace is not None                    # the run ended in its error handler


def test_an_error_inside_the_handler_ends_with_exit_error_without_recursion(graph, make_step):
    handler = make_step(boom("handler broke"), inputs={"cause": str}, done={}, parked={"reason": str})
    g = graph(HANDLED, {"x": make_step(boom(), inputs={"text": str}), "park": handler})

    result = g.run({"text": "t"})

    assert (result.exit, result.error.cause) == ("error", "step_error")
    assert result.error.handler_error.cause == "exception"
    assert "handler broke" in result.error.handler_error.message
    assert [c.step_id for c in g.dispatcher.calls] == ["p#x", "p#park"]
    assert len(g.events(result.run_id, "process.error")) == 1


def test_a_handler_exit_that_is_not_a_process_exit_is_a_handler_error(graph, make_step):
    handler = make_step(lambda i: {"exit": "odd"}, inputs={"cause": str}, done={}, odd={})
    g = graph(HANDLED, {"x": make_step(boom(), inputs={"text": str}), "park": handler})

    result = g.run({"text": "t"})

    assert result.exit == "error"
    assert result.error.handler_error.cause == "output_validation"
    assert "handler exit 'odd'" in result.error.handler_error.message


FINALLY = """
kind: process
name: p
entry: x
inputs: {text: string}
outputs: {}
steps:
  x: {use: ./steps/x}
  close: {use: ./steps/close}
  audit: {use: ./steps/audit}
  broken: {use: ./steps/broken}
edges:
  - {from: x.done, to: $exit.done}
finally:
  - close
  - step: audit
    with: {note: 'steps.x.exit + "/" + run.id', runs: steps.x.runs}
  - broken
"""


@pytest.mark.parametrize(("fails", "exit"), [(False, "done"), (True, "error")])
def test_finally_steps_run_in_order_and_never_change_the_exit(graph, make_step, fails, exit):
    got = []
    close = make_step(lambda i: got.append(("close", i.model_dump())) or {},
                      inputs={"text": str, "run_id": str, "exit": str})
    audit = make_step(lambda i: got.append(("audit", i.model_dump())) or {}, inputs={"note": str, "runs": int})
    x = make_step(boom() if fails else (lambda i: {}), inputs={"text": str})
    g = graph(FINALLY, {"x": x, "close": close, "audit": audit, "broken": make_step(boom("cleanup failed"))})

    result = g.run({"text": "t"}, run_id="run-fin")

    assert result.exit == exit
    assert got == [("close", {"text": "t", "run_id": "run-fin", "exit": exit}),
                   ("audit", {"note": f"{'error' if fails else 'done'}/run-fin", "runs": 1})]
    assert [e.cause for e in result.finally_errors] == ["exception"]
    assert "cleanup failed" in result.finally_errors[0].message
    roles = [(s["step"], s["role"], s["via"]) for s in g.events(result.run_id, "step.start")]
    assert roles[1:] == [("close", "finally", "finally"), ("audit", "finally", "finally"),
                         ("broken", "finally", "finally")]
    end = g.events(result.run_id, "run.end")[0]
    assert [e["cause"] for e in end["finally_errors"]] == ["exception"]
    assert (result.error is not None) == fails


FINALLY_BAD_WITH = """
kind: process
name: p
entry: x
inputs: {text: string}
outputs: {}
steps:
  x: {use: ./steps/x}
  audit: {use: ./steps/audit}
  close: {use: ./steps/close}
edges:
  - {from: x.done, to: $exit.done}
finally:
  - step: audit
    with: {note: 'steps.x.runs + "/" + run.id'}
  - close
"""


def test_a_finally_step_whose_with_fails_is_skipped_and_reported(graph, make_step):
    got = []
    audit = make_step(lambda i: got.append("audit") or {}, inputs={"note": str})
    close = make_step(lambda i: got.append("close") or {}, inputs={"text": str})
    g = graph(FINALLY_BAD_WITH, {"x": make_step(lambda i: {}, inputs={"text": str}), "audit": audit, "close": close})

    result = g.run({"text": "t"})

    assert (result.exit, result.status, result.error) == ("done", "succeeded", None)
    assert got == ["close"]
    assert [e.cause for e in result.finally_errors] == ["input_validation"]
    assert "finally step 'audit'" in result.finally_errors[0].message
    assert [s["step"] for s in g.events(result.run_id, "step.start")] == ["x", "close"]
    assert [s["step"] for s in g.events(result.run_id, "step.end")] == ["x", "close"]
    end = g.events(result.run_id, "run.end")[0]
    assert [e["cause"] for e in end["finally_errors"]] == ["input_validation"]


TIMED = """
kind: process
name: p
entry: a
outputs: {}
steps: {a: {use: ./steps/a}, b: {use: ./steps/b}}
edges:
  - from: a.done
    to: b
    with: {n: 7}
    limits: {timeout: TIMEOUT}
  - {from: b.done, to: $exit.done}
"""


@pytest.mark.parametrize(("timeout", "bound"), [(5, 5.0), ('"1 + 1"', 2.0)])
def test_branch_timeout_routes_to_the_process_handler(graph, make_step, timeout, bound):
    def hang(params, on_event):
        raise StepTimeout("no response")

    g = graph(TIMED.replace("TIMEOUT", str(timeout)), {"a": make_step(), "b": make_step(inputs={"n": int})},
              overrides={"b": hang})

    result = g.run()

    assert (result.exit, result.error.cause, result.error.step, result.error.edge) == (
        "error", "timeout", "b", "a.done[0]")
    assert result.error.inputs == {"n": 7} and result.error.step_error is None
    assert "exceeded its timeout" in result.error.message
    [call] = [c for c in g.dispatcher.calls if c.step_id == "p#b"]
    assert 0 < call.timeout <= bound
    b_end = g.events(result.run_id, "step.end")[1]
    assert (b_end["step"], b_end["exit"], b_end["timed_out"], b_end["outputs"]) == ("b", None, True, None)
    assert g.dispatcher.params_of("p#a")[0] is not None and g.dispatcher.calls[0].timeout is None


def test_a_worker_crash_is_a_routable_error_exit(graph, make_step):
    def crash(params, on_event):
        raise WorkerCrashed("worker exited", returncode=3)

    fallback = make_step(lambda i: {"text": f"{i.text} via {i.cause}"}, inputs={"text": str, "cause": str},
                         done={"text": str})
    g = graph(FALLBACK, {"x": make_step(inputs={"text": str}, done={"text": str}), "fallback": fallback},
              overrides={"x": crash})

    result = g.run({"text": "hi"})

    assert result.outputs == {"text": "hi via worker_crash"}
    x_end = g.events(result.run_id, "step.end")[0]
    assert (x_end["outputs"]["cause"], x_end["outputs"]["type"]) == ("worker_crash", "WorkerCrashed")


def test_an_unknown_provider_is_a_config_error_exit(graph, make_step):
    lock = StepLock(name="x", kind="agentic", entrypoint="x:X")
    g = graph(ONE, {"x": make_step(inputs={"text": str})}, provider="no-such-provider", kinds={"p#x": "agentic"},
              locks={"p#x": lock})

    result = g.run({"text": "t"})

    assert (result.error.cause, result.error.step_error.cause, result.error.step_error.attempts) == (
        "step_error", "config", 0)
    assert "no-such-provider" in result.error.step_error.message
    assert g.dispatcher.calls == []


def test_invalid_top_level_inputs_are_refused_before_anything_exists(graph, make_step, tmp_path):
    g = graph(ONE, {"x": make_step(inputs={"text": str})})

    with pytest.raises(InvalidProcessInputs) as info:
        g.run({"text": 1, "extra": True}, run_id="run-refused")

    assert {e["loc"][0] for e in info.value.errors} == {"text", "extra"}
    assert g.record("run-refused") is None
    assert g.stores.traces.read("run-refused") == []
    assert not (tmp_path / "data" / "traces").exists() and not (tmp_path / "data" / "workspaces").exists()


def test_a_successful_run_deletes_its_workspace_and_records_the_run(graph, make_step, step_result):
    doc = """
    kind: process
    name: p
    entry: a
    inputs: {text: string}
    outputs: {text: string}
    steps: {a: {use: ./steps/a}, b: {use: ./steps/b}}
    edges:
      - {from: a.done, to: b}
      - {from: b.done, to: $exit.done, with: {text: steps.a.outputs.text}}
    """
    usage = Usage(input_tokens=10, output_tokens=2, cost_usd=0.5, latency_ms=3.0, calls=1)
    model = ModelInfo(provider="fake", model_id="fake", tier="cheap", thinking="low")
    g = graph(doc, {"a": make_step(lambda i: {"text": i.text}, inputs={"text": str}, done={"text": str}),
                    "b": make_step()},
              kinds={"p#b": "agentic"}, locks={"p#b": StepLock(name="b", kind="agentic", entrypoint="b:B")},
              overrides={"b": lambda params, emit: step_result("b", usage=usage, model=model)})

    result = g.run({"text": "t"}, run_id="run-ok", metadata={"trigger": "manual", "release_id": None})

    assert (result.exit, result.status, result.workspace, result.error) == ("done", "succeeded", None, None)
    assert result.usage == usage and result.trace.startswith("file://")
    assert g.stores.workspaces.locate("run-ok") is None
    assert g.dispatcher.started == 1
    record = g.record("run-ok")
    assert {k: record[k] for k in ("status", "exit", "outputs", "error", "workspace", "mode", "process", "meta")} == {
        "status": "succeeded", "exit": "done", "outputs": {"text": "t"}, "error": None, "workspace": None,
        "mode": "local", "process": "p", "meta": {"trigger": "manual", "release_id": None}}
    assert record["usage"] == usage.model_dump(mode="json")
    assert record["started_at"] and record["finished_at"] and record["duration_ms"] > 0
    assert record["trace"] == result.trace
    assert record["trace_bytes"] == Path(urlparse(result.trace).path).stat().st_size
    assert record["workspace_bytes"] > 0                   # measured before the workspace was deleted
    start = g.events("run-ok", "run.start")[0]
    assert (start["process"], start["mode"], start["inputs"], start["cassette"], start["metadata"]) == (
        "p", "local", {"text": "t"}, "live", {"trigger": "manual", "release_id": None})
    b_end = g.events("run-ok", "step.end")[1]
    assert b_end["usage"] == usage.model_dump(mode="json") and b_end["model"]["tier"] == "cheap"


def test_a_sink_failure_mid_run_ends_the_run_with_cause_internal(graph, make_step):
    class FlakySink:
        def __init__(self, inner):
            self.inner, self.failed = inner, False

        def write(self, event):
            if event["type"] == "step.end" and not self.failed:
                self.failed = True
                raise OSError("disk full")
            self.inner.write(event)

        def close(self, run_id):
            self.inner.close(run_id)

        def read(self, run_id):
            return self.inner.read(run_id)

        def uri(self, run_id):
            return self.inner.uri(run_id)

    g = graph(ONE, {"x": make_step(inputs={"text": str})})
    g.stores = dataclasses.replace(g.stores, traces=FlakySink(g.stores.traces))

    result = g.run({"text": "t"})

    assert (result.exit, result.status, result.error.cause, result.error.step) == ("error", "failed", "internal", None)
    assert "disk full" in result.error.message
    assert g.record(result.run_id)["error"]["cause"] == "internal"
    assert g.events(result.run_id, "run.end")[0]["error"]["cause"] == "internal"


def test_one_executor_serves_concurrent_runs(graph, make_step, step_result):
    doc = """
    kind: process
    name: p
    entry: a
    inputs: {text: string}
    outputs: {text: string}
    steps: {a: {use: ./steps/a}, b: {use: ./steps/b}}
    edges:
      - {from: a.done, to: b, with: {text: steps.a.outputs.text}}
      - {from: b.done, to: $exit.done, with: {text: steps.b.outputs.text}}
    """

    def slow(params, emit):                                # the pool would run these in venv workers
        time.sleep(0.02)
        return step_result(params.step_path, outputs={"text": params.inputs["text"]})

    echo = make_step(inputs={"text": str}, done={"text": str})
    g = graph(doc, {"a": echo, "b": echo}, overrides={"a": slow, "b": slow})
    executor = g.executor()

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda n: executor.run({"text": f"t{n}"}, run_id=f"run-c{n}"), range(4)))

    assert [r.outputs for r in results] == [{"text": f"t{n}"} for n in range(4)]
    for n in range(4):
        events = g.events(f"run-c{n}")
        assert [e["seq"] for e in events] == list(range(1, 9))
        assert {e["run_id"] for e in events} == {f"run-c{n}"}
        assert g.record(f"run-c{n}")["status"] == "succeeded"

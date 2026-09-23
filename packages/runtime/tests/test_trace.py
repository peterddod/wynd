"""Trace events (PLAN §3.13; `$DRAFTS/02 §8, §13` test_trace): the golden JSONL of a three-step run, the emitter,
`parse_event` (typed and forward compatible), `build_tree` nesting and a failing `on_event` consumer."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from wynd.runtime.storage.local import JsonlTraceSink
from wynd.runtime.trace import (
    EdgeTaken,
    EventBase,
    ProcessErrorEvent,
    RunEnd,
    RunStart,
    StepEnd,
    StepStart,
    TraceEmitter,
    TraceNode,
    build_tree,
    parse_event,
    read_trace,
)
from wynd.runtime.usage import Usage
from wynd.spec.records import Summary

THREE = """
kind: process
name: p
entry: read
inputs: {text: string}
outputs: {text: string}
steps: {read: {use: ./steps/read}, upper: {use: ./steps/upper}, save: {use: ./steps/save}}
edges:
  - {from: read.done, to: upper, with: {text: steps.read.outputs.text}}
  - {from: upper.done, to: save, with: {text: steps.upper.outputs.text}}
  - {from: save.done, to: $exit.done, with: {text: steps.save.outputs.text}}
"""

TIMINGS = {k: "<t>" for k in ("started_at", "ended_at", "duration_ms", "worker_ms", "pre_ms", "run_ms", "post_ms")}


def _start(seq, step, via, text):
    return {"v": 1, "seq": seq, "ts": "<ts>", "run_id": "run-golden", "type": "step.start", "step": step, "name": step,
            "process": "p", "parent": None, "id": f"p#{step}", "kind": "deterministic", "run": 1, "role": "node",
            "via": via, "inputs": {"text": text}, "venv": "test", "span": seq}


def _end(seq, step, span, text):
    return {"v": 1, "seq": seq, "ts": "<ts>", "run_id": "run-golden", "type": "step.end", "step": step, "span": span,
            "parent": None, "run": 1, "kind": "deterministic", "exit": "done", "timed_out": False,
            "outputs": {"text": text},
            "summary": {"step": step, "exit": "done", "key_outputs": {"text": text}, "note": ""},
            "attempts": 1, "validation_failures": 0, "timings": TIMINGS, "usage": None, "model": None,
            "replayed": False}


def _taken(seq, source, to, text):
    return {"v": 1, "seq": seq, "ts": "<ts>", "run_id": "run-golden", "type": "edge.taken", "process": "p",
            "parent": None, "from": source, "branch": 0, "name": None, "to": to, "kind": "deterministic",
            "with": {"text": text}, "taken": 1}


GOLDEN = [
    {"v": 1, "seq": 1, "ts": "<ts>", "run_id": "run-golden", "type": "run.start", "process": "p", "mode": "local",
     "inputs": {"text": "abc"}, "commit": None, "runtime_version": "0.1.0", "workspace": "<workspace>",
     "cassette": "live", "metadata": {}},
    _start(2, "read", None, "abc"),
    {"v": 1, "seq": 3, "ts": "<ts>", "run_id": "run-golden", "type": "step.log", "level": "INFO", "stream": "logger",
     "message": "read 3 chars", "step": "read", "span": 2, "parent": None},
    _end(4, "read", 2, "abc"),
    _taken(5, "read.done", "upper", "abc"),
    _start(6, "upper", "read.done[0]", "abc"),
    _end(7, "upper", 6, "ABC"),
    _taken(8, "upper.done", "save", "ABC"),
    _start(9, "save", "upper.done[0]", "ABC"),
    _end(10, "save", 9, "ABC"),
    _taken(11, "save.done", "$exit.done", "ABC"),
    {"v": 1, "seq": 12, "ts": "<ts>", "run_id": "run-golden", "type": "run.end", "exit": "done",
     "outputs": {"text": "ABC"}, "error": None, "status": "succeeded", "duration_ms": "<ms>", "workspace_kept": False,
     "workspace": None, "usage": Usage().model_dump(mode="json"), "finally_errors": []},
]


def three_step(graph, make_step):
    def read(i):
        logging.getLogger("wynd.step.read").info("read %d chars", len(i.text))
        return {"text": i.text}

    return graph(THREE, {
        "read": make_step(read, inputs={"text": str}, done={"text": str}),
        "upper": make_step(lambda i: {"text": i.text.upper()}, inputs={"text": str}, done={"text": str}),
        "save": make_step(lambda i: {"text": i.text}, inputs={"text": str}, done={"text": str}),
    })


def normalise(event: dict) -> dict:
    event = {**event, "ts": "<ts>"}
    match event["type"]:
        case "run.start":
            event["workspace"] = "<workspace>"
        case "step.end":
            event["timings"] = {k: "<t>" for k in event["timings"]}
        case "run.end":
            event["duration_ms"] = "<ms>"
    return event


def test_golden_jsonl_of_a_three_step_run(graph, make_step):
    g = three_step(graph, make_step)

    result = g.run({"text": "abc"}, run_id="run-golden")

    events = g.events("run-golden")
    assert [normalise(e) for e in events] == GOLDEN
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    lines = Path(urlparse(result.trace).path).read_text().splitlines()
    assert [json.loads(line) for line in lines] == events              # one event per line, in seq order
    assert all(e["ts"].endswith("Z") for e in events)
    datetime.fromisoformat(events[0]["ts"])
    assert result.trace == g.stores.traces.uri("run-golden")


def test_on_event_sees_every_event_and_its_failure_never_breaks_the_run(graph, make_step):
    g = three_step(graph, make_step)
    seen = []

    def consumer(event):
        seen.append(event["seq"])
        raise RuntimeError("consumer bug")

    result = g.run({"text": "abc"}, on_event=consumer)

    assert (result.exit, result.outputs) == ("done", {"text": "ABC"})
    assert seen == [e["seq"] for e in g.events(result.run_id)] == list(range(1, 13))


def test_emitter_stamps_the_envelope_and_the_span(tmp_path):
    sink = JsonlTraceSink(tmp_path)
    forwarded = []
    emitter = TraceEmitter("run-e", sink, forwarded.append)

    first = emitter.emit("step.start", step="a", parent=None)
    second = emitter.emit("step.end", step="a", span=first["span"], summary=Summary(step="a", exit="done"),
                          usage=Usage(calls=1))

    assert (first["seq"], first["span"], second["seq"]) == (1, 1, 2)
    assert {k: first[k] for k in ("v", "run_id", "type")} == {"v": 1, "run_id": "run-e", "type": "step.start"}
    assert second["summary"] == {"step": "a", "exit": "done", "key_outputs": {}, "note": ""}
    assert second["usage"]["calls"] == 1                                # models are written JSON-mode
    assert forwarded == [first, second] == sink.read("run-e")


def test_parse_event_is_typed_and_forward_compatible():
    envelope = {"v": 1, "seq": 3, "ts": "2026-09-22T21:50:01.123456Z", "run_id": "r"}

    unknown = parse_event({**envelope, "type": "future.thing", "payload": [1, 2]})
    taken = parse_event({**envelope, "type": "edge.taken", "process": "p", "parent": None, "from": "a.done",
                         "branch": 1, "name": "retry", "to": "b", "kind": "deterministic", "with": {"x": 1},
                         "taken": 2})
    error = parse_event({**envelope, "type": "process.error", "process": "p", "parent": 4, "handler": "default",
                         "error": {"run_id": "r", "process": "p", "step": "a", "cause": "timeout", "message": "m"}})

    assert type(unknown) is EventBase and unknown.payload == [1, 2] and unknown.type == "future.thing"
    assert isinstance(taken, EdgeTaken) and (taken.from_, taken.with_, taken.name) == ("a.done", {"x": 1}, "retry")
    assert taken.model_dump(mode="json", by_alias=True)["from"] == "a.done"
    assert isinstance(error, ProcessErrorEvent) and error.error.cause == "timeout"
    assert isinstance(unknown.ts, datetime) and unknown.ts.tzinfo is not None
    assert parse_event(taken) is taken


NESTED_PARENT = """
kind: process
name: parent
entry: sub
inputs: {text: string}
outputs: {text: string}
steps: {sub: {use: 'process:child'}}
edges:
  - {from: sub.done, to: $exit.done, with: {text: steps.sub.outputs.text}}
"""

NESTED_CHILD = """
kind: process
name: child
entry: read
inputs: {text: string}
outputs: {text: string}
steps: {read: {use: ./steps/read}, shout: {use: ./steps/shout}}
edges:
  - {from: read.done, to: shout, with: {text: steps.read.outputs.text}}
  - {from: shout.done, to: $exit.done, with: {text: steps.shout.outputs.text}}
"""


def test_build_tree_nests_the_child_under_its_process_node_with_edges_interleaved(graph, make_step):
    def read(i):
        logging.getLogger("wynd.step.sub.read").warning("careful")
        return {"text": i.text}

    g = graph({"parent": NESTED_PARENT, "child": NESTED_CHILD}, {
        "child#read": make_step(read, inputs={"text": str}, done={"text": str}),
        "child#shout": make_step(lambda i: {"text": i.text.upper()}, inputs={"text": str}, done={"text": str}),
    })
    result = g.run({"text": "hi"})

    tree = build_tree(g.events(result.run_id))

    assert isinstance(tree.start, RunStart) and isinstance(tree.end, RunEnd) and tree.end.exit == "done"
    sub, parent_edge = tree.children
    assert isinstance(sub, TraceNode) and isinstance(sub.start, StepStart) and isinstance(sub.end, StepEnd)
    assert (sub.start.step, sub.end.exit, sub.events) == ("sub", "done", [])
    assert isinstance(parent_edge, EdgeTaken) and parent_edge.from_ == "sub.done"
    kinds = [(type(c).__name__, c.start.step if isinstance(c, TraceNode) else c.from_) for c in sub.children]
    assert kinds == [("TraceNode", "sub.read"), ("EdgeTaken", "read.done"), ("TraceNode", "sub.shout"),
                     ("EdgeTaken", "shout.done")]
    read_node = sub.children[0]
    assert [(e.type, e.message) for e in read_node.events] == [("step.log", "careful")]
    assert read_node.end.outputs == {"text": "hi"} and read_node.children == []


def test_build_tree_attaches_process_errors_under_their_instance(graph, make_step):
    def fail(_):
        raise RuntimeError("nope")

    g = graph({"parent": NESTED_PARENT, "child": NESTED_CHILD},
              {"child#read": make_step(fail, inputs={"text": str}, done={"text": str}),
               "child#shout": make_step(inputs={"text": str}, done={"text": str})})
    result = g.run({"text": "hi"})

    tree = build_tree(read_trace(g.stores.traces, result.run_id))

    sub, parent_error = tree.children
    assert isinstance(parent_error, ProcessErrorEvent) and parent_error.error.step == "sub"
    assert [type(c).__name__ for c in sub.children] == ["TraceNode", "ProcessErrorEvent"]
    assert sub.children[1].error.step == "sub.read" and sub.end.exit == "error"
    assert tree.end.status == "failed"

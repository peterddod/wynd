"""Trace events (JSONL, one line per event), the per-run emitter and display nesting (PLAN §3.13;
`$DRAFTS/02 §8`).

Producers and sinks exchange JSON-mode dicts; the models below are for readers. Every type allows extra fields, and
an unknown type parses to the envelope (forward compatible).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_core import to_jsonable_python

from wynd.runtime.usage import ModelInfo, Usage
from wynd.spec.lockfiles import TraceStepKind
from wynd.spec.records import ProcessError, StepError, Summary

if TYPE_CHECKING:
    from wynd.runtime.storage.base import TraceSink

_log = logging.getLogger(__name__)


class EventBase(BaseModel):
    """The envelope on every line."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    v: int = 1
    seq: int                                   # per run, from 1, contiguous
    ts: datetime                               # UTC, "2026-09-22T21:50:01.123456Z"
    run_id: str
    type: str


class RunStart(EventBase):
    type: Literal["run.start"] = "run.start"
    process: str
    mode: Literal["local", "image"]
    inputs: dict[str, Any] = {}
    commit: str | None = None
    runtime_version: str
    workspace: str | None = None
    cassette: Literal["live", "record", "replay"] = "live"
    metadata: dict[str, Any] = {}


class StepStart(EventBase):
    type: Literal["step.start"] = "step.start"
    step: str                                  # path, e.g. "sub.read"
    name: str                                  # node name
    process: str
    span: int                                  # = own seq
    parent: int | None = None
    id: str                                    # step id or "process:<pid>"
    kind: TraceStepKind
    run: int
    role: Literal["node", "on_error", "finally"] = "node"
    via: str | None = None                     # branch key | "on_error" | "finally" | None
    inputs: dict[str, Any] = {}
    venv: str | None = None


class StepEnd(EventBase):
    type: Literal["step.end"] = "step.end"
    step: str
    span: int
    parent: int | None = None
    run: int
    kind: TraceStepKind
    exit: str | None = None                    # None when timed out
    timed_out: bool = False
    outputs: dict[str, Any] | None = None      # exit "error": the StepError JSON without "exit"
    summary: Summary | None = None
    attempts: int = 0
    validation_failures: int = 0
    timings: dict[str, Any] = {}               # started_at, ended_at, duration_ms, worker_ms, pre_ms, run_ms, post_ms
    usage: Usage | None = None
    model: ModelInfo | None = None
    replayed: bool = False


class StepLog(EventBase):
    type: Literal["step.log"] = "step.log"
    step: str
    span: int | None = None
    parent: int | None = None
    level: str = "INFO"
    stream: Literal["logger", "output"] = "logger"
    message: str = ""


class StepEvent(EventBase):
    type: Literal["step.event"] = "step.event"
    step: str
    span: int | None = None
    parent: int | None = None
    name: str
    data: dict[str, Any] = {}


class EdgeTaken(EventBase):
    type: Literal["edge.taken"] = "edge.taken"
    process: str
    parent: int | None = None
    from_: str = Field(alias="from")           # edge key "validate.done"
    branch: int                                # index in `to:`
    name: str | None = None
    to: str                                    # node name, "$exit.<x>" or "$ignore"
    kind: Literal["deterministic", "agentic"] = "deterministic"
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    taken: int                                 # count after this take
    verdicts: list[dict[str, Any]] | None = None   # agentic edges only: [{branch, take, reason}]


class EdgeCheck(EventBase):
    type: Literal["edge.check"] = "edge.check"
    process: str
    parent: int | None = None
    edge: str
    branch: int
    branch_key: str
    target: str
    take: bool | None = None                   # None when the check failed
    reason: str | None = None
    attempts: int | None = None
    validation_failures: int | None = None
    error_cause: str | None = None
    provider: str | None = None
    tier: str | None = None
    model_id: str | None = None
    usage: Usage | None = None
    duration_ms: float | None = None
    replayed: bool | None = None


class ModelCall(EventBase):
    """Payload per `$DRAFTS/03 §6.8` (owned by the agentic loop); only the stamped fields are typed."""

    type: Literal["model.call"] = "model.call"
    step: str                                  # path, or "edge:<branch_key>"
    span: int | None = None
    parent: int | None = None


class ToolCall(EventBase):
    """Payload owned by the agentic loop; only the stamped fields are typed."""

    type: Literal["tool.call"] = "tool.call"
    step: str
    span: int | None = None
    parent: int | None = None


class ProcessErrorEvent(EventBase):
    type: Literal["process.error"] = "process.error"
    process: str
    parent: int | None = None
    error: ProcessError
    handler: str                               # "default" or the on_error step key


class WorkerStart(EventBase):
    type: Literal["worker.start"] = "worker.start"
    step: str
    span: int | None = None
    parent: int | None = None
    venv: str
    pid: int | None = None
    startup_ms: float | None = None


class RunEnd(EventBase):
    type: Literal["run.end"] = "run.end"
    exit: str
    outputs: dict[str, Any] = {}
    error: ProcessError | None = None
    status: Literal["succeeded", "failed"]
    duration_ms: float
    workspace_kept: bool = False
    workspace: str | None = None
    usage: Usage = Usage()
    finally_errors: list[StepError] = []


EVENT_TYPES: dict[str, type[EventBase]] = {
    cls.model_fields["type"].default: cls
    for cls in (RunStart, StepStart, StepEnd, StepLog, StepEvent, EdgeTaken, EdgeCheck, ModelCall, ToolCall,
                ProcessErrorEvent, WorkerStart, RunEnd)
}


def parse_event(data: Mapping[str, Any] | EventBase) -> EventBase:
    """Parse one event dict into its model; unknown types parse to the envelope (forward compatible)."""
    if isinstance(data, EventBase):
        return data
    return EVENT_TYPES.get(data.get("type"), EventBase).model_validate(data)


class TraceEmitter:
    """Stamps `v/seq/ts/run_id/type` (and `span = seq` on `step.start`), writes to the sink, forwards to `on_event`.

    Events are JSON-mode dicts. An `on_event` exception is logged, never propagated; a sink exception propagates."""

    def __init__(self, run_id: str, sink: TraceSink, on_event: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.run_id = run_id
        self.sink = sink
        self.on_event = on_event
        self._seq = 0
        self._lock = threading.Lock()

    def emit(self, type: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            event = {"v": 1, "seq": self._seq, "ts": _timestamp(), "run_id": self.run_id, "type": type,
                     **to_jsonable_python(fields)}
            if type == "step.start":
                event["span"] = self._seq
            self.sink.write(event)
            if self.on_event is not None:
                try:
                    self.on_event(event)
                except Exception:  # noqa: BLE001 — a consumer's failure never breaks the run
                    _log.exception("trace on_event callback failed on %s (seq %d)", type, self._seq)
        return event


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass
class TraceNode:
    """One step span: its `step.start`, `step.end`, attached events and nested children."""

    start: StepStart
    end: StepEnd | None = None
    events: list[EventBase] = field(default_factory=list)   # step.log/.event, model.call, tool.call, worker.start
    children: list[TraceNode | EventBase] = field(default_factory=list)   # nested nodes, edge events, process.error


@dataclass
class TraceTree:
    """A run: `run.start`, `run.end` and the top-level children, in seq order."""

    start: RunStart | None = None
    end: RunEnd | None = None
    children: list[TraceNode | EventBase] = field(default_factory=list)


def read_trace(sink: TraceSink, run_id: str) -> list[EventBase]:
    return [parse_event(event) for event in sink.read(run_id)]


def build_tree(events: Iterable[Mapping[str, Any] | EventBase]) -> TraceTree:
    """Nest by span: `step.start` opens a node under its `parent` span; an event carrying a known `span` belongs to
    that node; anything else (edge.taken, edge.check, process.error, edge model calls) is attached under `parent`."""
    tree = TraceTree()
    spans: dict[int, TraceNode] = {}

    def attach(item: TraceNode | EventBase, parent: int | None) -> None:
        node = spans.get(parent) if parent is not None else None
        (node.children if node is not None else tree.children).append(item)

    for event in sorted((parse_event(e) for e in events), key=lambda e: e.seq):
        match event:
            case RunStart():
                tree.start = event
            case RunEnd():
                tree.end = event
            case StepStart():
                node = spans[event.span] = TraceNode(event)
                attach(node, event.parent)
            case StepEnd() if event.span in spans:
                spans[event.span].end = event
            case _:
                span = getattr(event, "span", None)
                if span in spans:
                    spans[span].events.append(event)
                else:
                    attach(event, getattr(event, "parent", None))
    return tree

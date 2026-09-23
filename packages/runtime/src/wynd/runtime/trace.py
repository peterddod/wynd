"""Trace events (JSONL, one line per event), the per-run emitter and display nesting (PLAN §3.13;
`$DRAFTS/02 §8`)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.runtime.storage.base import TraceSink


def parse_event(data: dict[str, Any]) -> Any:
    """Parse one event dict into its model; unknown types parse to the envelope (forward compatible)."""
    raise NotImplementedError("PLAN §3.13")


class TraceEmitter:
    """Stamps `v/seq/ts/run_id/type` (and `span` on `step.start`), writes to the sink, forwards to `on_event`."""

    def __init__(self, run_id: str, sink: TraceSink, on_event: Callable[[dict[str, Any]], None] | None = None) -> None:
        raise NotImplementedError("PLAN §3.13")

    def emit(self, type: str, **fields: Any) -> Any:
        raise NotImplementedError("PLAN §3.13")


class TraceNode:
    """One step span: its `step.start`, `step.end`, attached events and nested children."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §3.13")


class TraceTree:
    """A run: `run.start`, `run.end` and the top-level children."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §3.13")


def read_trace(sink: TraceSink, run_id: str) -> list[Any]:
    raise NotImplementedError("PLAN §3.13")


def build_tree(events: Iterable[Any]) -> TraceTree:
    raise NotImplementedError("PLAN §3.13")

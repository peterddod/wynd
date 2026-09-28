"""`render_tree` over runtime `build_tree` (PLAN §8.1; format `$DRAFTS/06 §5.10`, M5 `edge.check` line
`$DRAFTS/08 §4.7`).

One line per step node (`#n` when `run > 1`): exit, duration, usage (`model in→out tok $cost`) and
`summary.key_outputs` as `k=v` (80 chars); taken branches other than an unnamed first one as
`→ <branch key> → <to>`; an `error` exit adds `cause: <cause>: <message>`; `full` adds `in:`/`out:` JSON (400 chars
each). `render_events` builds the tree first (the CLI imports only `wynd.controller`).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from wynd.runtime.trace import (
    EdgeCheck,
    EdgeTaken,
    EventBase,
    ProcessErrorEvent,
    StepEnd,
    TraceNode,
    TraceTree,
    build_tree,
)
from wynd.spec.lockfiles import branch_key

__all__ = ["render_tree", "render_events", "build_tree"]

NAME_WIDTH = 19          # tree prefix + step label
EXIT_WIDTH = 7
TIME_WIDTH = 8
EXTRAS_LIMIT = 80
FULL_LIMIT = 400


def render_tree(tree: TraceTree, *, full: bool = False) -> str:
    lines = [_header(tree)]
    _items(tree.children, "", lines, full)
    return "\n".join(lines)


def render_events(events: Iterable[Mapping[str, Any]], *, full: bool = False) -> str:
    return render_tree(build_tree(events), full=full)


def _header(tree: TraceTree) -> str:
    start, end = tree.start, tree.end
    parts = [f"run {start.run_id if start else '?'}", start.process if start else "?", start.mode if start else "?"]
    if end is None:
        parts.append("running")
    else:
        parts += [f"exit={end.exit}", _duration(end.duration_ms)]
        if end.usage.cost_usd:
            parts.append(_cost(end.usage.cost_usd))
    return "  ".join(parts)


def _items(items: list[TraceNode | EventBase], prefix: str, lines: list[str], full: bool) -> None:
    last_node = max((i for i, item in enumerate(items) if isinstance(item, TraceNode)), default=-1)
    for index, item in enumerate(items):
        if isinstance(item, TraceNode):
            is_last = index == last_node
            lines.append(_node_line(prefix + ("└─ " if is_last else "├─ "), item))
            inner = prefix + ("   " if is_last else "│  ")
            lines += [inner + detail for detail in _details(item, full)]
            _items(item.children, inner, lines, full)
            continue
        text = _event_line(item)
        if text is not None:
            lines.append(prefix + ("│  " if index < last_node else "   ") + text)


def _node_line(head: str, node: TraceNode) -> str:
    start, end = node.start, node.end
    label = head + (f"{start.name} #{start.run}" if start.run > 1 else start.name)
    exit, duration, extras = "…", "", ""
    if end is not None:
        exit = "timeout" if end.timed_out else end.exit or "-"
        duration = _duration(end.timings.get("duration_ms"))
        extras = " ".join(x for x in (_usage(end), _key_outputs(end)) if x)
    line = f"{_pad(label, NAME_WIDTH)}{_pad(exit, EXIT_WIDTH)}{_pad(duration, TIME_WIDTH)}{extras}"
    return line.rstrip()


def _details(node: TraceNode, full: bool) -> list[str]:
    end = node.end
    out = []
    if end is not None and end.exit == "error" and end.outputs:
        message = str(end.outputs.get("message", "")).splitlines()
        out.append(f"cause: {end.outputs.get('cause')}: {message[0] if message else ''}")
    if full:
        out.append(f"in: {_clip(_json(node.start.inputs), FULL_LIMIT)}")
        if end is not None:
            out.append(f"out: {_clip(_json(end.outputs), FULL_LIMIT)}")
    return out


def _event_line(event: EventBase) -> str | None:
    match event:
        case EdgeTaken() if event.branch > 0 or event.name:
            return f"→ {branch_key(event.from_, event.branch, event.name)} → {event.to}"
        case EdgeCheck():
            match event.take:
                case True:
                    verdict = "TAKEN"
                case False:
                    verdict = "NOT TAKEN"
                case _:
                    verdict = f"FAILED ({event.error_cause})"
            reason = f' — "{event.reason}"' if event.reason else ""
            facts = [f"{event.provider}/{event.tier}"] if event.provider else []
            if event.duration_ms is not None:
                facts.append(_duration(event.duration_ms))
            if event.usage is not None and event.usage.cost_usd:
                facts.append(_cost(event.usage.cost_usd))
            suffix = f" ({', '.join(facts)})" if facts else ""
            return f"{event.edge} → {event.target}  check: {verdict}{reason}{suffix}"
        case ProcessErrorEvent():
            error = event.error
            where = f" at {error.step}" if error.step else ""
            return f"✗ {error.cause}{where}: {error.message} (handler: {event.handler})"
    return None


def _usage(end: StepEnd) -> str:
    usage, model = end.usage, end.model
    if usage is None or not usage.calls:
        return ""
    parts = [model.model_id] if model is not None and model.model_id else []
    parts.append(f"{usage.input_tokens}→{usage.output_tokens} tok")
    if usage.cost_usd:
        parts.append(_cost(usage.cost_usd))
    return " ".join(parts)


def _key_outputs(end: StepEnd) -> str:
    if end.summary is None or not end.summary.key_outputs:
        return ""
    text = " ".join(f"{k}={_value(v)}" for k, v in end.summary.key_outputs.items())
    return _clip(text, EXTRAS_LIMIT)


def _value(value: Any) -> str:
    """Plain text as is; anything else, or text with line breaks or tabs, as JSON (one tree line per step)."""
    return value if isinstance(value, str) and value.isprintable() else _json(value)


def _duration(ms: float | None) -> str:
    if ms is None:
        return ""
    return f"{ms:.0f}ms" if ms < 1000 else f"{ms / 1000:.2f}s"


def _cost(usd: float) -> str:
    return f"${usd:.4f}"


def _pad(text: str, width: int) -> str:
    return text.ljust(width) if len(text) < width else text + " "


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

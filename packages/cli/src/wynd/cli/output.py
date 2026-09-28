"""CLI output helpers (PLAN §9, §3.22; `$DRAFTS/06 §9.1` "Output").

Human output on stdout, progress and logs on stderr; `--json` prints exactly one JSON document on stdout (a model's
`model_dump(mode="json")`, or `{"items": [...]}` for a list). Tables are plain columns separated by two spaces; short
shas are 7 characters; durations print as `14ms`, `1.21s`, `2m03s`; usage as `haiku 812→64 tok $0.0011`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import PurePath
from typing import Any

import typer


def echo(text: str = "") -> None:
    typer.echo(text)


def err(text: str) -> None:
    typer.echo(text, err=True)


def jsonable(value: Any) -> Any:
    """Pydantic models (duck-typed: the CLI does not import pydantic) via `model_dump(mode="json")`; containers
    recursively; dates as ISO text; paths as text."""
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    match value:
        case Mapping():
            return {str(key): jsonable(item) for key, item in value.items()}
        case list() | tuple():
            return [jsonable(item) for item in value]
        case datetime() | date():
            return value.isoformat()
        case PurePath():
            return str(value)
    return value


def print_json(value: Any) -> None:
    """Exactly one JSON document on stdout; a list is wrapped as `{"items": [...]}`."""
    data = jsonable(value)
    if isinstance(data, list):
        data = {"items": data}
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """Left-aligned columns separated by two spaces; `None` cells print as `-`."""
    cells = [[str(h) for h in headers]] + [["-" if c is None else str(c) for c in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in cells)


def fmt_sha(sha: str | None) -> str:
    return "-" if not sha else sha[:7]


def fmt_duration(ms: float | None) -> str:
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms:.0f}ms"
    if ms < 60_000:
        return f"{ms / 1000:.2f}s"
    minutes, seconds = divmod(round(ms / 1000), 60)
    return f"{minutes}m{seconds:02d}s"


def fmt_usage(usage: Any, model: str | None = None) -> str:
    """`<model> <in>→<out> tok $<cost>`; "" when there is no usage. `usage` is a `Usage` model or its JSON."""
    if usage is None:
        return ""
    data = jsonable(usage)
    if not data.get("calls") and not data.get("input_tokens") and not data.get("output_tokens"):
        return ""
    parts = [model] if model else []
    parts.append(f"{data.get('input_tokens', 0)}→{data.get('output_tokens', 0)} tok")
    if data.get("cost_usd"):
        parts.append(f"${data['cost_usd']:.4f}")
    return " ".join(parts)


def fmt_age(at: datetime | None, now: datetime) -> str:
    """Time since `at`, coarsely: `45s`, `12m`, `3h`, `2d`."""
    if at is None:
        return "-"
    seconds = max(0, int((now - at).total_seconds()))
    for unit, size in (("d", 86_400), ("h", 3_600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def yes(flag: bool) -> str:
    return "yes" if flag else "-"

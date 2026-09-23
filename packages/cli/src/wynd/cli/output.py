"""CLI output helpers (PLAN §9, §3.22; `$DRAFTS/06 §9.1` "Output"). Stub; CLI-M1.

Human output on stdout, progress and logs on stderr; `--json` prints exactly one JSON document on stdout. Tables are
plain columns separated by two spaces; short shas are 7 characters; durations print as `14ms`, `1.21s`, `2m03s`; usage
as `haiku 812→64 tok $0.0011`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def echo(text: str = "") -> None:
    raise NotImplementedError("PLAN §9")


def err(text: str) -> None:
    raise NotImplementedError("PLAN §9")


def print_json(value: Any) -> None:
    raise NotImplementedError("PLAN §9")


def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    raise NotImplementedError("PLAN §9")


def fmt_sha(sha: str | None) -> str:
    raise NotImplementedError("PLAN §9")


def fmt_duration(ms: float | None) -> str:
    raise NotImplementedError("PLAN §9")


def fmt_usage(usage: Any, model: str | None = None) -> str:
    raise NotImplementedError("PLAN §9")

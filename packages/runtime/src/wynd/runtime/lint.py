"""Static (AST-only) lint of step modules against module/class-level state: `L001`–`L005`, all errors
(PLAN §5.1 lint row; `$DRAFTS/02 §3.7`)."""

from __future__ import annotations

from typing import Any


class LintIssue:
    """`path, line, col, code, severity, message`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.1")


def check_step_module(source: str, path: str) -> list[LintIssue]:
    raise NotImplementedError("PLAN §5.1")

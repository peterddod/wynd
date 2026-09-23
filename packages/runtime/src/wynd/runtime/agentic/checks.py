"""Definition-time checks of AgenticStep classes and their description for `describe` (PLAN §5.2;
`$DRAFTS/03 §6.1`): docstring present, `run` body is `...`, tools decorated, unique tool names, valid context entries,
`@tool` methods not named run/pre/post."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def is_ellipsis_body(fn: Callable[..., Any]) -> bool:
    raise NotImplementedError("PLAN §5.1")


def check_agentic_class(cls: type) -> None:
    """Raises `StepDefinitionError` listing every problem."""
    raise NotImplementedError("PLAN §5.1")


def describe_agentic(cls: type) -> dict[str, Any]:
    raise NotImplementedError("PLAN §5.1")

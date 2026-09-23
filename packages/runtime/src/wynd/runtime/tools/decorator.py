"""`@tool` and tool introspection (SPEC §3.8, PLAN §5.2; `$DRAFTS/03 §9.1`). The decorator only marks the
function, which stays directly callable."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, Literal


class ToolDecl:
    """What `@tool(...)` attaches to a function: name, effects, idempotent, env (and `allow` for `shell.allow`)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.1")


class ToolSpec:
    """A resolved tool: name, description, effects, idempotent, env, source, input schema, args model."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.1")


def tool(
    fn: Callable[..., Any] | None = None,
    /,
    *,
    name: str | None = None,
    effects: Iterable[str] = (),
    idempotent: bool = False,
    env: Iterable[str] = (),
) -> Callable[..., Any]:
    """`@tool` or `@tool(effects=["network"], idempotent=True, env=["CRM_TOKEN"])`."""
    raise NotImplementedError("PLAN §5.2")


def tool_spec(fn: Callable[..., Any], source: Literal["builtin", "method"]) -> ToolSpec:
    raise NotImplementedError("PLAN §5.1")


def step_tools(cls: type) -> list[ToolSpec]:
    raise NotImplementedError("PLAN §5.1")


def env(name: str) -> str:
    """A secret/config reference resolved at run time; unset -> `MissingEnvVar` -> `StepFailure("config")`."""
    raise NotImplementedError("PLAN §5.2")

"""`@tool` and tool introspection (SPEC §3.8, PLAN §5.2; `$DRAFTS/03 §9.1`). The decorator only marks the
function, which stays directly callable; `tool_spec`/`step_tools` turn marked functions into `ToolSpec`s (argument
model and JSON schema from the signature, description from the docstring)."""

from __future__ import annotations

import inspect
import os
import re
import typing
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, create_model

from wynd.runtime.agentic.errors import MissingEnvVar
from wynd.runtime.errors import StepDefinitionError
from wynd.spec.lockfiles import Effect, ToolSnapshot

EFFECTS = ("network", "filesystem", "shell")
TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class ToolDecl:
    """What `@tool(...)` attaches to a function (`fn.__wynd_tool__`). `shell.allow(...)` returns one directly, with
    `allow` (the permitted executables) and `fn` (the builtin bound to that allowlist) set."""

    name: str | None
    effects: tuple[Effect, ...]
    idempotent: bool
    env: tuple[str, ...]
    allow: tuple[str, ...] = ()
    fn: Callable[..., Any] | None = None

    @property
    def __wynd_tool__(self) -> ToolDecl:
        """A declaration is its own marker, so `getattr(x, "__wynd_tool__", None)` finds functions and decls alike."""
        return self


@dataclass(frozen=True)
class ToolSpec:
    """A resolved tool. `fn` is the function to call with the validated arguments (unbound for methods; None for
    MCP tools, which the ToolSet calls through the server's client)."""

    name: str
    description: str
    effects: tuple[Effect, ...]
    idempotent: bool
    env: tuple[str, ...]
    source: Literal["builtin", "method", "mcp"]
    server: str | None                      # mcp only
    input_schema: dict[str, Any]            # JSON Schema (from the args model, or the MCP snapshot)
    args_model: type[BaseModel] | None      # None for mcp
    returns: TypeAdapter | None             # None for mcp / missing return annotation
    fn: Callable[..., Any] | None = None
    allow: tuple[str, ...] = ()             # shell.allow(...) only

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "effects": list(self.effects),
            "idempotent": self.idempotent,
            "env": list(self.env),
            "source": self.source,
            "input_schema": self.input_schema,
        }

    def snapshot(self) -> ToolSnapshot:
        """The `step.lock.yaml` entry (builtins are source `library`)."""
        if self.source == "mcp":
            raise ValueError(f"tool {self.name}: MCP tools are snapshotted per server (McpSnapshot)")
        return ToolSnapshot(
            name=self.name,
            source="method" if self.source == "method" else "library",
            effects=list(self.effects),
            idempotent=self.idempotent,
            env=list(self.env),
            allow=list(self.allow),
        )


def tool(
    fn: Callable[..., Any] | None = None,
    /,
    *,
    name: str | None = None,
    effects: Iterable[str] = (),
    idempotent: bool = False,
    env: Iterable[str] = (),
) -> Callable[..., Any]:
    """`@tool` or `@tool(effects=["network"], idempotent=True, env=["CRM_TOKEN"])`. Works on methods, plain functions
    and closures; only marks the function (`fn.__wynd_tool__ = ToolDecl(...)`)."""
    if name is not None and not TOOL_NAME.match(name):
        raise StepDefinitionError(f"tool name {name!r} must match {TOOL_NAME.pattern}")
    values = tuple(effects)
    unknown = [e for e in values if e not in EFFECTS]
    if unknown:
        raise StepDefinitionError(f"unknown tool effect {unknown[0]!r}; effects are {', '.join(EFFECTS)}")
    decl = ToolDecl(name=name, effects=values, idempotent=bool(idempotent), env=tuple(env))

    def mark(f: Callable[..., Any]) -> Callable[..., Any]:
        f.__wynd_tool__ = decl
        return f

    return mark if fn is None else mark(fn)


def tool_spec(fn: Callable[..., Any], source: Literal["builtin", "method"]) -> ToolSpec:
    """The spec of a `@tool` function (or a `ToolDecl` from `shell.allow`). For `source="method"` the first
    parameter (`self`) is not an argument."""
    decl = getattr(fn, "__wynd_tool__", None)
    if not isinstance(decl, ToolDecl):
        raise StepDefinitionError(f"{getattr(fn, '__qualname__', fn)!r} is not decorated with @tool")
    return _spec(fn, decl, source, None)


def step_tools(cls: type) -> list[ToolSpec]:
    """The tools of a step class: its `@tool` methods (source "method", definition order) then `cls.tools` (source
    "builtin"). An undecorated entry or a bare `shell` in `tools` is a `StepDefinitionError`."""
    from wynd.runtime.tools.builtins import shell

    members: dict[str, Any] = {}
    for klass in reversed(cls.__mro__):
        members.update(vars(klass))
    specs = [
        _spec(value, value.__wynd_tool__, "method", members)
        for value in members.values()
        if inspect.isfunction(value) and isinstance(getattr(value, "__wynd_tool__", None), ToolDecl)
    ]
    for i, item in enumerate(getattr(cls, "tools", None) or []):
        if item is shell:
            raise StepDefinitionError(
                'tools: the shell builtin needs an allowlist of executables: shell.allow("<executable>", ...)'
            )
        decl = getattr(item, "__wynd_tool__", None)
        if not isinstance(decl, ToolDecl):
            label = getattr(item, "__qualname__", item)
            raise StepDefinitionError(f"tools[{i}] ({label!r}) is not decorated with @tool")
        specs.append(_spec(item, decl, "builtin", None))
    return specs


def env(name: str) -> str:
    """A secret/config reference resolved at run time; unset (or empty) -> `MissingEnvVar`, which resolves the step
    with cause `config`."""
    value = os.environ.get(name)
    if not value:
        raise MissingEnvVar(name)
    return value


def _spec(target: Any, decl: ToolDecl, source: Literal["builtin", "method"],
          localns: Mapping[str, Any] | None) -> ToolSpec:
    fn = decl.fn if target is decl else target
    name = decl.name or fn.__name__
    description = inspect.getdoc(fn)
    if not description:
        raise StepDefinitionError(f"tool {name} needs a docstring (it is the description the model sees)")
    try:
        hints = typing.get_type_hints(fn, localns=dict(localns or {}), include_extras=True)
    except Exception as e:
        raise StepDefinitionError(f"tool {name}: cannot resolve its type hints: {e}") from e
    params = list(inspect.signature(fn).parameters.values())
    if source == "method":
        params = params[1:]
    fields: dict[str, Any] = {}
    for p in params:
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            raise StepDefinitionError(f"tool {name}: *args/**kwargs parameters are not supported")
        fields[p.name] = (hints.get(p.name, Any), ... if p.default is p.empty else p.default)
    try:
        args_model = create_model(f"{name}_args", __config__=ConfigDict(extra="forbid"), **fields)
        input_schema = args_model.model_json_schema()
        returns = TypeAdapter(hints["return"]) if "return" in hints else None
    except Exception as e:
        raise StepDefinitionError(f"tool {name}: {e}") from e
    return ToolSpec(
        name=name,
        description=description,
        effects=decl.effects,
        idempotent=decl.idempotent,
        env=decl.env,
        source=source,
        server=None,
        input_schema=input_schema,
        args_model=args_model,
        returns=returns,
        fn=fn,
        allow=decl.allow,
    )

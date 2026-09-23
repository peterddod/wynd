"""Definition-time checks of AgenticStep classes and their description for `describe` (PLAN §5.2;
`$DRAFTS/03 §6.1`): docstring present, `run` body is `...`, tools decorated, unique tool names, valid context entries,
`@tool` methods not named run/pre/post."""

from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Callable
from typing import Any

from wynd.runtime.errors import StepDefinitionError

HOOKS = ("run", "pre", "post")


def is_ellipsis_body(fn: Callable[..., Any]) -> bool:
    """True iff the function body is `...`, optionally preceded by a docstring. Raises OSError/TypeError when the
    source cannot be read."""
    node = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    body = node.body
    if body and _is_constant(body[0], str):
        body = body[1:]
    return len(body) == 1 and _is_constant(body[0], type(Ellipsis))


def check_agentic_class(cls: type) -> None:
    """Raises `StepDefinitionError` listing every problem."""
    problems = _docstring_problems(cls) + _run_problems(cls) + _hook_tool_problems(cls)
    tools_ok = _tools_problems(cls, problems)
    mcp_ok = _mcp_problems(cls, problems)
    _context_problems(cls, problems)
    if tools_ok and mcp_ok:
        _tool_name_problems(cls, problems)
    if problems:
        raise StepDefinitionError(
            f"{cls.__name__} is not a valid AgenticStep:\n" + "\n".join(f"  - {p}" for p in problems)
        )


def describe_agentic(cls: type) -> dict[str, Any]:
    """`{"instruction", "context", "tools": [ToolSpec.describe()...], "mcp": [{"name", "allow"}],
    "completed_by_loop": True}` for the worker's `describe` (compiler, validator, web)."""
    from wynd.runtime.tools.decorator import step_tools

    return {
        "instruction": step_instruction(cls),
        "context": list(getattr(cls, "context", [])),
        "tools": [spec.describe() for spec in step_tools(cls)],
        "mcp": [{"name": server.name, "allow": list(server.allow)} for server in getattr(cls, "mcp", [])],
        "completed_by_loop": True,
    }


def step_instruction(cls: type) -> str:
    """The instruction: the cleaned docstring of the class or of its nearest user-defined ancestor (the docstrings of
    `AgenticStep` and its bases do not count); "" if there is none."""
    from wynd.runtime.step import AgenticStep

    for klass in cls.__mro__:
        if klass is AgenticStep:
            break
        doc = klass.__dict__.get("__doc__")
        if isinstance(doc, str) and doc.strip():
            return inspect.cleandoc(doc)
    return ""


def _is_constant(stmt: ast.stmt, kind: type) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, kind)


def _docstring_problems(cls: type) -> list[str]:
    if step_instruction(cls):
        return []
    return ["the class docstring is the instruction: add a docstring describing the task"]


def _run_problems(cls: type) -> list[str]:
    run = cls.__dict__.get("run")
    if run is None:
        return []
    try:
        if is_ellipsis_body(run):
            return []
    except (OSError, TypeError):
        return ["cannot read the source of run(); ship .py sources in the wheel"]
    return ["AgenticStep.run must be `...` (completed by the loop); put deterministic logic in a DeterministicStep or "
            "a @tool method"]


def _hook_tool_problems(cls: type) -> list[str]:
    return [
        f"@tool method {name}() is named like a step hook (run/pre/post); rename it"
        for name in HOOKS
        if any(getattr(klass.__dict__.get(name), "__wynd_tool__", None) is not None for klass in cls.__mro__)
    ]


def _tools_problems(cls: type, problems: list[str]) -> bool:
    tools = getattr(cls, "tools", [])
    if not isinstance(tools, (list, tuple)):
        problems.append(f"tools must be a list of @tool functions, got {type(tools).__name__}")
        return False
    before = len(problems)
    for i, item in enumerate(tools):
        if getattr(item, "__wynd_tool__", None) is None:
            problems.append(f"tools[{i}] ({getattr(item, '__qualname__', item)!r}) is not decorated with @tool")
    return len(problems) == before


def _mcp_problems(cls: type, problems: list[str]) -> bool:
    from wynd.runtime.mcp import McpServer

    servers = getattr(cls, "mcp", [])
    if not isinstance(servers, (list, tuple)):
        problems.append(f"mcp must be a list of McpServer(...), got {type(servers).__name__}")
        return False
    before = len(problems)
    seen: set[str] = set()
    for i, server in enumerate(servers):
        if not isinstance(server, McpServer):
            problems.append(f"mcp[{i}] must be McpServer(name, allow=[...]), got {type(server).__name__}")
        elif server.name in seen:
            problems.append(f"mcp: server {server.name!r} is declared twice")
        else:
            seen.add(server.name)
    return len(problems) == before


def _context_problems(cls: type, problems: list[str]) -> None:
    from wynd.spec.context import parse_context_entry

    context = getattr(cls, "context", [])
    if not isinstance(context, (list, tuple)):
        problems.append(f"context must be a list of strings, got {type(context).__name__}")
        return
    for i, entry in enumerate(context):
        if not isinstance(entry, str):
            problems.append(f"context[{i}] must be a string, got {type(entry).__name__}")
            continue
        try:
            parse_context_entry(entry)
        except ValueError as err:
            problems.append(f"context[{i}]: {err}")


def _tool_name_problems(cls: type, problems: list[str]) -> None:
    """Names are unique across @tool methods, `tools` and `<server>__<tool>` of every allowed MCP tool."""
    from wynd.runtime.tools.decorator import step_tools

    try:
        names = [spec.name for spec in step_tools(cls)]
    except StepDefinitionError as err:
        problems.append(str(err))
        return
    names += [f"{server.name}__{tool}" for server in getattr(cls, "mcp", []) for tool in server.allow]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    for name in duplicates:
        problems.append(f"tool name {name!r} is used more than once (tool names are unique across @tool methods, "
                        f"tools and MCP tools <server>__<tool>)")

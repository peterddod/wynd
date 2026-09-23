"""Static checks and extraction on generated step modules (`$DRAFTS/05 §9.7`, PLAN §7 item 11).

`wynd.runtime.lint.check_step_module` runs first and every `L` finding is an error; each error is fed back into a
revision like the compiler-specific ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wynd.compiler.tools import ToolCatalog
    from wynd.spec.lockfiles import Effect


@dataclass
class ToolMethodInfo:
    """One `@tool(...)` method: its decorator arguments."""
    name: str
    effects: list[Effect]
    idempotent: bool
    env: list[str]


@dataclass
class StaticReport:
    errors: list[str]
    env_vars: set[str]
    tools: list[str]                          # names in `tools = [...]`
    mcp: list[tuple[str, list[str]]]          # McpServer("x", allow=[...]) literals
    context: list[str]
    tool_methods: list[ToolMethodInfo]


def check(source: str, *, class_name: str, base: Literal["DeterministicStep", "AgenticStep", "ShellStep"],
          catalog: ToolCatalog, upstream_nodes: list[str]) -> StaticReport:
    raise NotImplementedError("PLAN §7")

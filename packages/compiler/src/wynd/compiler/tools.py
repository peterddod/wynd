"""Tool selection, MCP discovery, `allow` narrowing and the lock's tool/MCP snapshots (`$DRAFTS/05 §7.7`).

Preference order: runtime builtins, then `@tool` methods, then registry MCP servers with the narrowest `allow` that
covers the examples (measured from the `tool.call` events of record runs).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.compiler.astcheck import StaticReport
    from wynd.runtime.mcp.entry import McpServerEntry
    from wynd.runtime.mcp.snapshot import McpToolSpec
    from wynd.runtime.storage.base import Registry
    from wynd.spec.lockfiles import McpSnapshot, ToolSnapshot


class BuiltinToolInfo:
    """A `wynd.runtime.tools` builtin as offered to codegen (name, signature, effects). Stub: lands with CMP-C."""


class McpServerInfo:
    """A registry MCP server with its discovered tools. Stub: lands with CMP-C."""


@dataclass
class ToolCatalog:
    builtins: list[BuiltinToolInfo]
    mcp: list[McpServerInfo]                   # discovered once per job


def build_catalog(registry: Registry, list_tools: Callable[[McpServerEntry], list[McpToolSpec]]) -> ToolCatalog:
    """A discovery failure omits the server and warns."""
    raise NotImplementedError("PLAN §7")


def lock_tools(static: StaticReport, catalog: ToolCatalog) -> tuple[list[ToolSnapshot], list[McpSnapshot]]:
    raise NotImplementedError("PLAN §7")


def used_tools(events_file: Path) -> set[tuple[str, str]]:
    """(server, tool) pairs from the `tool.call` events in a `WYND_EVENTS_FILE`."""
    raise NotImplementedError("PLAN §7")


def narrow_allow(mcp: list[McpSnapshot], used: set[tuple[str, str]]) -> list[McpSnapshot] | None:
    """None when there is nothing to narrow."""
    raise NotImplementedError("PLAN §7")

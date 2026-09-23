"""MCP discovery and snapshots (compile time) and verification against a live server (run time) (PLAN §3.6, §5.5;
`$DRAFTS/03 §10.4–§10.5`). The snapshot shape is the spec `McpSnapshot`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.runtime.mcp import McpServer
    from wynd.runtime.mcp.client import McpClient
    from wynd.runtime.mcp.entry import McpServerEntry
    from wynd.spec.lockfiles import McpSnapshot


@dataclass(frozen=True)
class McpToolSpec:
    """One tool as a live server lists it."""

    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any]


def list_tools(entry: McpServerEntry, env: Mapping[str, str] | None = None) -> list[McpToolSpec]:
    raise NotImplementedError("PLAN §5.1")


def schema_sha256(name: str, input_schema: dict[str, Any]) -> str:
    raise NotImplementedError("PLAN §5.1")


def snapshot(server: McpServer, tools: list[McpToolSpec]) -> McpSnapshot:
    raise NotImplementedError("PLAN §5.1")


def verify(snap: McpSnapshot, client: McpClient) -> None:
    """Raises `McpSnapshotMismatch` naming every missing tool or changed input schema."""
    raise NotImplementedError("PLAN §5.1")

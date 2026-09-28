"""MCP discovery and snapshots (compile time) and verification against a live server (run time) (PLAN §3.6, §5.5;
`$DRAFTS/03 §10.4–§10.5`). The snapshot shape is the spec `McpSnapshot`: exactly the allowed tools, sorted by name,
with their input schemas; run time compares `(name, input_schema)` only (descriptions may change freely)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from wynd.runtime.agentic.errors import McpSnapshotMismatch
from wynd.runtime.mcp.entry import open_client
from wynd.spec.hashing import hash_obj
from wynd.spec.lockfiles import McpSnapshot, McpToolSnapshot

if TYPE_CHECKING:
    from wynd.runtime.mcp import McpServer
    from wynd.runtime.mcp.client import McpClient
    from wynd.runtime.mcp.entry import McpServerEntry


@dataclass(frozen=True)
class McpToolSpec:
    """One tool as a live server lists it."""

    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any]
    output_schema: dict[str, Any] | None = None


def list_tools(entry: McpServerEntry, env: Mapping[str, str] | None = None) -> list[McpToolSpec]:
    """Connect (env references resolved from `env`, default `os.environ`), list every tool, disconnect."""
    client = open_client(entry, os.environ if env is None else env)
    try:
        raw = client.list_tools()
    finally:
        client.close()
    return [
        McpToolSpec(
            name=t["name"],
            description=t.get("description") or "",
            input_schema=t.get("inputSchema") or {"type": "object"},
            annotations=t.get("annotations") or {},
            output_schema=t.get("outputSchema"),
        )
        for t in raw
    ]


def schema_sha256(name: str, input_schema: dict[str, Any]) -> str:
    """"sha256:<hex>" of the canonical `{name, input_schema}` (key order does not matter)."""
    return hash_obj({"name": name, "input_schema": input_schema})


def snapshot(server: McpServer, tools: list[McpToolSpec]) -> McpSnapshot:
    """The lock block for `server`: its allowed tools, sorted by name. `idempotent` comes from the tool's
    `idempotentHint` or `readOnlyHint` annotation. An allowed name the server lacks -> ValueError."""
    by_name = {t.name: t for t in tools}
    for name in server.allow:
        if name not in by_name:
            raise ValueError(f"MCP server {server.name!r} has no tool {name!r}; available: {sorted(by_name)}")
    snaps = [
        McpToolSnapshot(
            name=t.name,
            description=t.description,
            input_schema=t.input_schema,
            output_schema=t.output_schema,
            idempotent=bool(t.annotations.get("idempotentHint") or t.annotations.get("readOnlyHint")),
        )
        for t in sorted((by_name[n] for n in set(server.allow)), key=lambda t: t.name)
    ]
    return McpSnapshot(
        server=server.name,
        allow=list(server.allow),
        tools=snaps,
        hash=hash_obj([t.model_dump(mode="json") for t in snaps]),
    )


def verify(snap: McpSnapshot, client: McpClient) -> None:
    """Raises `McpSnapshotMismatch` naming every missing tool or changed input schema."""
    live = {t["name"]: t for t in client.list_tools()}
    problems = []
    for tool in snap.tools:
        if tool.name not in live:
            problems.append(f"{tool.name}: missing on the server")
        elif schema_sha256(tool.name, live[tool.name].get("inputSchema") or {"type": "object"}) != schema_sha256(
            tool.name, tool.input_schema
        ):
            problems.append(f"{tool.name}: input schema changed")
    if problems:
        raise McpSnapshotMismatch(
            f"MCP server {snap.server!r} no longer matches the snapshot in step.lock.yaml:\n"
            + "\n".join(f"  - {p}" for p in problems)
            + "\nRe-run `wynd compile` to re-snapshot, and review the diff."
        )

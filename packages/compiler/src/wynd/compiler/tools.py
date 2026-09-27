"""Tool selection, MCP discovery, `allow` narrowing and the lock's tool/MCP snapshots (`$DRAFTS/05 §7.7`).

Preference order: runtime builtins, then `@tool` methods, then registry MCP servers with the narrowest `allow` that
covers the examples (measured from the `tool.call` events of record runs).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wynd.spec.hashing import hash_obj
from wynd.spec.lockfiles import McpSnapshot, ToolSnapshot

if TYPE_CHECKING:
    from wynd.compiler.astcheck import StaticReport
    from wynd.runtime.mcp.entry import McpServerEntry
    from wynd.runtime.mcp.snapshot import McpToolSpec
    from wynd.runtime.storage.base import Registry
    from wynd.runtime.tools.decorator import ToolSpec


@dataclass
class BuiltinToolInfo:
    """A `wynd.runtime.tools` builtin as offered to codegen (name, signature, effects)."""
    name: str
    signature: str                       # "web_search(query: string, count: integer = 5)"
    description: str
    effects: list[str]
    idempotent: bool
    env: list[str]

    @classmethod
    def from_spec(cls, spec: ToolSpec) -> BuiltinToolInfo:
        return cls(name=spec.name, signature=f"{spec.name}({_arguments(spec.input_schema)})",
                   description=spec.description, effects=list(spec.effects), idempotent=spec.idempotent,
                   env=list(spec.env))

    def line(self) -> str:
        facts = [f"effects: {', '.join(self.effects) or 'none'}"]
        if self.idempotent:
            facts.append("idempotent")
        if self.env:
            facts.append(f"env: {', '.join(self.env)}")
        if self.name == "shell":
            facts.append('list it as shell.allow("<executable>", ...) naming the programs it may run')
        return f"- {self.signature} — {self.description} [{'; '.join(facts)}]"


@dataclass
class McpServerInfo:
    """A registry MCP server with its discovered tools."""
    name: str
    entry: McpServerEntry
    tools: list[McpToolSpec]

    def summary(self) -> dict[str, Any]:
        return {"description": self.entry.description, "tools": {t.name: t.description for t in self.tools}}


@dataclass
class ToolCatalog:
    builtins: list[BuiltinToolInfo]
    mcp: list[McpServerInfo]                   # discovered once per job
    warnings: list[str] = field(default_factory=list)

    def builtin(self, name: str) -> BuiltinToolInfo | None:
        return next((b for b in self.builtins if b.name == name), None)

    def server(self, name: str) -> McpServerInfo | None:
        return next((s for s in self.mcp if s.name == name), None)


def build_catalog(registry: Registry, list_tools: Callable[[McpServerEntry], list[McpToolSpec]], *,
                  builtins: Sequence[ToolSpec] | None = None) -> ToolCatalog:
    """A discovery failure omits the server and warns (`ToolCatalog.warnings`). `builtins` defaults to
    `wynd.runtime.tools.BUILTINS`."""
    from wynd.runtime.mcp.entry import McpServerEntry

    if builtins is None:
        from wynd.runtime.tools import BUILTINS as builtins
    catalog = ToolCatalog(builtins=[BuiltinToolInfo.from_spec(s) for s in builtins], mcp=[])
    for name, raw in sorted(registry.list("mcp").items()):
        try:
            entry = McpServerEntry.model_validate({**raw, "name": name})
            tools = list_tools(entry)
        except Exception as err:  # noqa: BLE001 — any discovery failure only drops that server
            catalog.warnings.append(f"MCP server {name} is not offered to the compiler: discovery failed ({err})")
            continue
        catalog.mcp.append(McpServerInfo(name=name, entry=entry, tools=sorted(tools, key=lambda t: t.name)))
    return catalog


def builtin_catalog() -> ToolCatalog:
    """The runtime builtins only (no registry servers)."""
    from wynd.runtime.tools import BUILTINS

    return ToolCatalog(builtins=[BuiltinToolInfo.from_spec(s) for s in BUILTINS], mcp=[])


def lock_tools(static: StaticReport, catalog: ToolCatalog) -> tuple[list[ToolSnapshot], list[McpSnapshot]]:
    """Snapshots in describe order: `@tool` methods, then the `tools` builtins, then one `McpSnapshot` per
    `McpServer(...)` (its allowed tools, sorted). An unknown builtin, server or tool is a `ValueError` (astcheck
    reports these first)."""
    from wynd.runtime.mcp import McpServer
    from wynd.runtime.mcp.snapshot import snapshot

    tools = [ToolSnapshot(name=m.name, source="method", effects=list(m.effects), idempotent=m.idempotent,
                          env=list(m.env)) for m in static.tool_methods]
    for name in static.tools:
        info = catalog.builtin(name)
        if info is None:
            raise ValueError(f"unknown builtin tool {name!r}")
        allow = list(static.shell_allow) if name == "shell" else []
        tools.append(ToolSnapshot(name=name, source="library", effects=list(info.effects),
                                  idempotent=info.idempotent, env=list(info.env), allow=allow))
    snapshots = []
    for server, allow in static.mcp:
        info = catalog.server(server)
        if info is None:
            raise ValueError(f"unknown MCP server {server!r}")
        snapshots.append(snapshot(McpServer(server, allow=allow), info.tools))
    return tools, snapshots


def used_tools(events_file: Path) -> set[tuple[str, str]]:
    """(server, tool) pairs from the `tool.call` events in a `WYND_EVENTS_FILE`."""
    path = Path(events_file)
    if not path.is_file():
        return set()
    used = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        source = event.get("source") or ""
        if event.get("type") == "tool.call" and source.startswith("mcp:"):
            used.add((source[len("mcp:"):], event.get("tool")))
    return used


def narrow_allow(mcp: list[McpSnapshot], used: set[tuple[str, str]]) -> list[McpSnapshot] | None:
    """Each server's `allow` cut down to the tools the record run called; a server left with none is dropped.
    None when there is nothing to narrow."""
    out = []
    changed = False
    for snap in mcp:
        keep = [name for name in snap.allow if (snap.server, name) in used]
        if keep == list(snap.allow):
            out.append(snap)
            continue
        changed = True
        if not keep:
            continue
        tools = [t for t in snap.tools if t.name in keep]
        out.append(McpSnapshot(server=snap.server, allow=keep, tools=tools,
                               hash=hash_obj([t.model_dump(mode="json") for t in tools])))
    return out if changed else None


def _arguments(schema: dict[str, Any]) -> str:
    required = set(schema.get("required") or [])
    out = []
    for name, prop in (schema.get("properties") or {}).items():
        text = f"{name}: {_type_name(prop)}"
        if name not in required and "default" in prop:
            text += f" = {json.dumps(prop['default'])}"
        out.append(text)
    return ", ".join(out)


def _type_name(prop: dict[str, Any]) -> str:
    if "anyOf" in prop:
        return " | ".join(_type_name(p) for p in prop["anyOf"])
    kind = prop.get("type", "any")
    if kind == "array":
        return f"list[{_type_name(prop.get('items') or {})}]"
    return kind

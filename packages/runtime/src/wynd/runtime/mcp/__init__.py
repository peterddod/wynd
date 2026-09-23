"""MCP for agentic steps (SPEC §3.8, PLAN §5.1; `$DRAFTS/03 §10`): the `McpServer` declaration, registry entries,
the stdlib client, compile-time snapshots and run-time verification. Other names are re-exported lazily."""

from __future__ import annotations

import importlib
from collections.abc import Iterable

_MODULES = {
    ".entry": ("McpServerEntry", "resolve_env_refs", "open_client"),
    ".snapshot": ("McpToolSpec", "list_tools", "snapshot", "schema_sha256", "verify"),
}
_EXPORTS = {name: module for module, names in _MODULES.items() for name in names}
__all__ = ["McpServer", *_EXPORTS]


class McpServer:
    """`McpServer("github", allow=["get_issue"])` in an AgenticStep's `mcp`; `allow` is required and non-empty."""

    name: str
    allow: tuple[str, ...]

    def __init__(self, name: str, *, allow: Iterable[str]) -> None:
        raise NotImplementedError("PLAN §5.2")


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})

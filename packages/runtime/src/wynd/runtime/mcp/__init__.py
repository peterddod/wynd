"""MCP for agentic steps (SPEC §3.8, PLAN §5.1; `$DRAFTS/03 §10`): the `McpServer` declaration, registry entries,
the stdlib client, compile-time snapshots and run-time verification. Other names are re-exported lazily."""

from __future__ import annotations

import importlib
from collections.abc import Iterable
from dataclasses import dataclass

from wynd.runtime.errors import StepDefinitionError

_MODULES = {
    ".entry": ("McpServerEntry", "resolve_env_refs", "open_client"),
    ".snapshot": ("McpToolSpec", "list_tools", "snapshot", "schema_sha256", "verify"),
}
_EXPORTS = {name: module for module, names in _MODULES.items() for name in names}
__all__ = ["McpServer", *_EXPORTS]


@dataclass(frozen=True, init=False)
class McpServer:
    """`McpServer("github", allow=["get_issue"])` in an AgenticStep's `mcp`; `allow` is required and non-empty."""

    name: str
    allow: tuple[str, ...]

    def __init__(self, name: str, *, allow: Iterable[str]) -> None:
        if isinstance(allow, str):
            raise StepDefinitionError(f"McpServer({name!r}): allow must be a list of tool names, not a string")
        names = tuple(allow)
        if not names:
            raise StepDefinitionError(
                "McpServer(allow=...) must name at least one tool; exposing a server's entire tool surface is not "
                "permitted"
            )
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "allow", names)


# The submodule `snapshot` shares its name with the function: the first import of the submodule would set this
# package's `snapshot` attribute to the module, so the function is bound eagerly (a later import leaves it alone).
from wynd.runtime.mcp.snapshot import snapshot  # noqa: E402, F401


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})

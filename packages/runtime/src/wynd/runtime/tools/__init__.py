"""Tools for agentic steps: the `@tool` decorator, the per-run `ToolSet` and the builtin library (SPEC §3.8,
PLAN §5.1; `$DRAFTS/03 §9`). Names are re-exported lazily."""

import importlib

_MODULES = {
    ".decorator": ("tool", "env", "ToolDecl", "ToolSpec", "tool_spec", "step_tools"),
    ".toolset": ("ToolSet", "is_transient", "current_runtime", "handles_for"),
    ".builtins": ("http_get", "web_search", "workspace_read", "workspace_write", "shell", "now", "BUILTINS"),
    "wynd.runtime.agentic.errors": ("ToolInputError",),
}
_EXPORTS = {name: module for module, names in _MODULES.items() for name in names}
__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})

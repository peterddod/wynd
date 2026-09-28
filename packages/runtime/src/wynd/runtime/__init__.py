"""wynd.runtime: everything that runs inside a process image.

The step-author API is re-exported lazily (a leaf module is imported on first access). This module must never
import the executor, storage, worker or supervisor.
"""

import importlib

__version__ = "0.1.0"

_MODULES = {
    ".step": ("Step", "DeterministicStep", "AgenticStep", "ShellStep", "ProcessStep"),
    ".shell": ("ShellResult",),
    ".tools.decorator": ("tool", "env"),
    ".mcp": ("McpServer",),
    ".handle": ("RuntimeHandle",),
    "wynd.spec.records": ("StepError", "ProcessError", "Summary"),
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

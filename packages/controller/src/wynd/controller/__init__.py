"""wynd.controller: the library behind the `wynd` CLI and the HTTP API.

`Controller`, `ControllerContext` and the error hierarchy are re-exported lazily: a leaf module is imported on first
access.
"""

import importlib

__version__ = "0.1.0"

_MODULES = {
    ".controller": ("Controller", "ControllerContext"),
    ".errors": (
        "WyndError", "Invalid", "ValidationFailed", "Unauthorized", "OutOfScope", "NotFound", "NotAWorkspace",
        "Conflict", "DirtyTree", "RevisionConflict", "NotBuilt", "EnvMissing", "EnvUnbound", "JobState",
        "DetachedHead", "TurnInProgress", "VersionMismatch", "DesignLocked", "Unavailable",
    ),
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

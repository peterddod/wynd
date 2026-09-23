"""wynd.compiler: compile sessions and the generate-test-revise loop (proto-steps to step packages).

Public names are re-exported lazily: a leaf module is imported on first access.
"""

import importlib

__version__ = "0.1.0"

_MODULES = {
    ".jobs": ("run_compile_job",),
    ".session": (
        "CompileSession", "SessionData", "SessionState", "Question", "Answer", "SessionEvent", "CompileStep",
        "CompileOptions",
    ),
    ".report": ("CompileReport",),
    ".testing": ("ScriptedLLM", "RecordingLLM", "FakeJobContext"),
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

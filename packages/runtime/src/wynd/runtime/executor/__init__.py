"""The executor: one sequential state machine over a `RunPlan`, local and image alike (SPEC §8, PLAN §5.4).

Names are re-exported lazily so importing `wynd.runtime.executor.edges` never loads the engine.
"""

import importlib

_MODULES = {
    ".engine": ("Executor", "ProcessResult"),
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

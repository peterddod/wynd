"""The agentic loop: structured completion over ModelProviders (runtime-owned loop) and AgentProviders (harness)
(PLAN §5.5; `$DRAFTS/03 §6`). Names are re-exported lazily."""

import importlib

_MODULES = {
    ".loop": ("complete", "complete_structured", "StructuredCall"),
    ".checks": ("check_agentic_class", "describe_agentic"),
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

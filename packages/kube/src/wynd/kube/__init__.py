"""wynd.kube: Wynd on Kubernetes (job runner, triggers, serving backend, install manifests, `wynd-kube` CLI).

The controller selects these backends through entry points and never imports this package. The backend classes are
re-exported lazily: a leaf module is imported on first access.
"""

import importlib

__version__ = "0.1.0"

_MODULES = {
    ".runner": ("KubeJobRunner",),
    ".triggers": ("KubeTriggerBackend",),
    ".serving": ("KubeServingBackend",),
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

"""wynd.process.build: the image build job (prepare/finalize), resolution, step wheels, Dockerfile, process lock.

Public names are re-exported lazily: a leaf module is imported on first access.
"""

import importlib

_MODULES = {
    ".job": ("Prepared", "prepare_build_job", "finalize_build_job", "run_build_job"),
    ".resolve": ("Resolver", "UvResolver"),
    ".wheels": ("stage_step", "build_step_wheel"),
    ".variant": ("choose_base",),
    ".dockerfile": ("render_dockerfile",),
    ".lockfile": ("make_process_lock",),
    ".imagebuilder": (
        "ImageBuildRequest", "ImageBuildResult", "ImageBuilder", "BuildxImageBuilder", "open_image_builder",
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

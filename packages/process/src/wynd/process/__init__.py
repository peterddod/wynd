"""wynd.process: workspace loading, validation, venvs, local runs, tests, git, jobs, build artefacts.

Public names are re-exported lazily: a leaf module is imported on first access. `validate`, `validate_process` and
`ValidationReport` come from the `validation` subpackage, so `wynd.process.validate` is always the function.
"""

import importlib

__version__ = "0.1.0"

_MODULES = {
    ".workspace": ("find_workspace_root", "load_workspace", "Workspace"),
    ".loader": ("LoadedProcess", "ResolvedStep", "StepPackage", "ResolvedInterface"),
    ".validation": ("validate", "validate_process", "ValidationReport"),
    ".plan": ("plan_local",),
    ".local": ("run_local",),
    ".testing": ("run_tests", "tests_status"),
    ".envmanifest": ("assemble_env_manifest",),
    ".compile": ("compile_state", "CompileState"),
    ".git": ("reference_closure", "closure_head", "dirty_paths", "integrate", "IntegrationResult"),
    ".jobs": ("JobKind", "JobStatus", "JobRecord", "JobContext", "JobOutcome", "JobUsage", "JobRunner"),
    ".artefacts": ("open_artefact_store", "ArtefactStore", "BuildInfo", "ImageRegistryEntry"),
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

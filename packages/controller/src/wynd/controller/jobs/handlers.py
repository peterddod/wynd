"""Job handler table (PLAN §3.18)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.process.jobs import JobHandler

DEFAULT_HANDLERS: dict[str, str] = {
    "compile": "wynd.compiler.jobs:run_compile_job",
    "test_live": "wynd.process.testing:run_test_live_job",
    "build": "wynd.process.build.job:run_build_job",
    "bake": "wynd.process.bake:run_bake_job",
    "optimise": "wynd.controller.optimise:run_optimise_job",
}

# Phased execution (kube build Jobs): the prepare result is stored as artefacts["prepared"].
PHASE_HANDLERS: dict[str, dict[str, str]] = {
    "build": {
        "prepare": "wynd.process.build.job:prepare_build_job",
        "finalize": "wynd.process.build.job:finalize_build_job",
    },
}


def resolve_handler(spec: str) -> JobHandler:
    """`"module:function"` -> the handler, imported on call (M1 works before the compiler exists)."""
    module, sep, name = spec.partition(":")
    if not sep or not module or not name:
        raise ValueError(f"job handler {spec!r} is not of the form 'module:function'")
    handler = getattr(importlib.import_module(module), name, None)
    if not callable(handler):
        raise ValueError(f"job handler {spec!r}: module {module!r} has no function {name!r}")
    return handler

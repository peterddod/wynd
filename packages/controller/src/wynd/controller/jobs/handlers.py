"""Job handler table (PLAN §3.18). `resolve_handler` is a stub; CTL-JOBS."""

from __future__ import annotations

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
    raise NotImplementedError("PLAN §3.18")

"""Bake a process into a single executable (PLAN §6.1, §15 item 47; owner PROC-BAKE, M2; `$DRAFTS/04 §8.9`).

stdlib zipapp + extract-once bootstrap (`templates/bake_main.py`); bakeable iff no `system:` packages, no shell steps
and one resolvable environment. Shares the build prepare phase, so a bake is commit-pinned and test-gated.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .build.job import Prepared
    from .jobs import JobContext, JobOutcome


def run_bake_job(ctx: JobContext) -> JobOutcome:
    raise NotImplementedError("PLAN §6.1 run_bake_job")


def bake_process(ctx: JobContext, prepared: Prepared) -> Path:
    raise NotImplementedError("PLAN §6.1 bake_process")

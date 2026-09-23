"""M5 optimise: report, submit and the `optimise` job handler, plus its two API routes (PLAN §8.1 optimise row;
`$DRAFTS/08 §3.7-§3.8` adapted to PLAN §3.18: `ctx.commit` instead of `ctx.git`, tests via `wynd.process.testing`).
Stub; OPT-CTL.

`router` (`GET/POST /api/processes/{pid}/optimise`) is empty until M5 and is mounted by `api/app.py` before the
catch-all process routes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

if TYPE_CHECKING:
    from wynd.controller.controller import Controller
    from wynd.process.jobs import JobContext, JobOutcome
    from wynd.process.optimise import OptimiseReport

router = APIRouter()


def optimise_report(ctl: Controller, process_id: str, *, max_runs: int = 200, min_runs: int = 20) -> OptimiseReport:
    raise NotImplementedError("PLAN §8.1")


def submit_optimise(
    ctl: Controller,
    process_id: str,
    *,
    units: list[str] | None = None,
    max_runs: int = 200,
    min_runs: int = 20,
) -> tuple[str, OptimiseReport]:
    """-> (job_id, report); `ctl.jobs.submit("optimise", ...)`."""
    raise NotImplementedError("PLAN §8.1")


def run_optimise_job(ctx: JobContext) -> JobOutcome:
    """The `optimise` job handler (`DEFAULT_HANDLERS["optimise"]`)."""
    raise NotImplementedError("PLAN §8.1")

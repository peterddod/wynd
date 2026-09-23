"""`ProcessService` (PLAN §8.1; `$DRAFTS/06 §5.4`). Stub; CTL-CORE.

StepInfo/ProcessInterface DTOs are built from `LoadedProcess` (source mapping lock->"compiled", proto->"declared",
examples->"inferred", process->"process", none->null; `kind: TraceStepKind`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import (
        Build,
        CommitInfo,
        ExprCheck,
        ExprCheckRequest,
        FileContent,
        ProcessInterface,
        ProcessStatus,
        ProcessSummary,
        StatusFlag,
        StepCatalogEntry,
        StepInfo,
        StepSource,
        ValidationReportDTO,
    )
    from wynd.process.testing import TestReport


class ProcessService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl

    def list(self, q: str = "", flags: Sequence[StatusFlag] = ()) -> list[ProcessSummary]:
        raise NotImplementedError("PLAN §8.1")

    def get(self, pid: str) -> ProcessSummary:
        raise NotImplementedError("PLAN §8.1")

    def new(self, pid: str, *, goal: str | None = None, root: str | None = None, commit: bool = True) -> ProcessSummary:
        raise NotImplementedError("PLAN §8.1")

    def status(self, pid: str) -> ProcessStatus:
        raise NotImplementedError("PLAN §8.1")

    def validate(self, pid: str) -> ValidationReportDTO:
        raise NotImplementedError("PLAN §8.1")

    def validate_all(self) -> dict[str, ValidationReportDTO]:
        raise NotImplementedError("PLAN §8.1")

    def steps(self, pid: str) -> dict[str, StepInfo]:
        raise NotImplementedError("PLAN §8.1")

    def step_catalog(self) -> list[StepCatalogEntry]:
        raise NotImplementedError("PLAN §8.1")

    def interface(self, pid: str, commit: str | None = None) -> ProcessInterface:
        raise NotImplementedError("PLAN §8.1")

    def builds(self, pid: str) -> list[Build]:
        raise NotImplementedError("PLAN §8.1")

    def read_file(self, pid: str, path: str) -> FileContent:
        raise NotImplementedError("PLAN §8.1")

    def step_source(self, pid: str, step: str) -> StepSource:
        raise NotImplementedError("PLAN §8.1")

    def history(self, pid: str, limit: int = 50) -> list[CommitInfo]:
        raise NotImplementedError("PLAN §8.1")

    def test(self, pid: str, *, log: Callable[[str], None] | None = None) -> TestReport:
        """Replay tests, not a job; results are recorded only when the closure is clean (PLAN §3.20)."""
        raise NotImplementedError("PLAN §8.1")

    def sync_interfaces(self, pid: str) -> list[str]:
        """`wynd validate --sync-interfaces`: refresh lock `interface`/`context`/`shell.exit_codes` snapshots; -> the
        lock files changed."""
        raise NotImplementedError("PLAN §8.1")

    def check_expr(self, req: ExprCheckRequest) -> ExprCheck:
        raise NotImplementedError("PLAN §8.1")

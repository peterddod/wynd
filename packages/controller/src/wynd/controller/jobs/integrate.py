"""Controller wrapper around `wynd.process.git.integrate` (PLAN §3.18 "Integration"). Stub; CTL-JOBS.

Holds the git lock, fetches result branches from `WYND_GIT_REMOTE` when set, copies test results to the new commit
after `rebased`, and stores the `IntegrationResult` on the job record. Idempotent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.controller.controller import ControllerContext
    from wynd.process.git import IntegrationResult
    from wynd.process.jobs import JobRecord


def integrate(ctx: ControllerContext, job: JobRecord) -> IntegrationResult:
    raise NotImplementedError("PLAN §3.18")

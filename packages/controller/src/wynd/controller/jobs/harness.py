"""The job harness `execute_job` (PLAN §3.18 "Harness contract"; `$DRAFTS/06 §6.4`). Stub; CTL-JOBS.

Load the record; `status=running`; checkout via `CheckoutBackend.prepare`; build `JobContext`; run the handler
(`phase="all"`: the handler table; `"prepare"`: `PHASE_HANDLERS[kind]["prepare"]`, whose result is stored as
`artefacts["prepared"]` while the record stays `running`; `"finalize"`: `PHASE_HANDLERS[kind]["finalize"]`); publish
`result_branch(job_kind, process, id)` at `outcome.commit`; fold the outcome into the record; remove the checkout on
success/awaiting_input and keep it on failure; a handler exception -> `failed` with the traceback in the log.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wynd.controller.jobs.checkout import CheckoutBackend
    from wynd.process.jobs import JobRecord
    from wynd.runtime.storage import Stores
    from wynd.runtime.storage.base import Registry


class JobCancelled(Exception):
    """Raised inside a running job when it is cancelled (the subprocess worker maps SIGTERM to it)."""


def execute_job(
    job_id: str,
    *,
    phase: Literal["all", "prepare", "finalize"] = "all",
    checkout: CheckoutBackend,
    stores: Stores,
    registry: Registry,
    handlers: Mapping[str, str] | None = None,
) -> JobRecord:
    """-> the final `JobRecord`. `handlers` overrides the record's handler per job kind (tests)."""
    raise NotImplementedError("PLAN §3.18")

"""Process diagnostics codes and the process exception hierarchy (PLAN §3.2, §6.1; owner PROC-WS).

`CODES` maps the §3.2 process loader/validator codes to `(severity, message template)`; templates from
`$DRAFTS/04 §4.12` (with the §3.2 `W128` override and the new `E223`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.spec.errors import Severity

    from .validation import ValidationReport

CODES: dict[str, tuple[Severity, str]] = {}


class WyndProcessError(Exception):
    """Base class of every error raised by `wynd.process`."""


class LoadError(WyndProcessError):
    """A document needed to build the process model could not be loaded (YAML or schema errors)."""


class ProcessNotFound(WyndProcessError):
    """No process with the requested id exists in the workspace (`E127`)."""


class DesignPhase(WyndProcessError):
    """Closure steps are still in the design phase (proto-only or stale), so the process cannot be planned or built."""

    def __init__(self, step_ids: Sequence[str]) -> None:
        self.step_ids = list(step_ids)
        super().__init__(f"process is in design phase: steps {', '.join(self.step_ids)} are not compiled")


class ValidationFailed(WyndProcessError):
    """Validation produced at least one error; `report` holds every diagnostic."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__(f"process '{report.process}' failed validation")


class ToolMissing(WyndProcessError):
    """A required external executable (`git`, `uv`, `docker`) is not installed."""


class GitError(WyndProcessError):
    """A git command failed."""


class ResolutionError(WyndProcessError):
    """Dependency resolution (`uv pip compile`) failed."""

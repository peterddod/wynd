"""Workspace scaffolding: `init_workspace`, `new_process_files` (PLAN §8.1; `$DRAFTS/06 §5.4`). Stub; CTL-CORE.

`init_workspace` writes `wynd.yaml`, the `.gitignore` lines `.wynd/`, `.env`, `__pycache__/`, `.pytest_cache/` and the
`.gitattributes` cassette LFS line; `new_process_files` returns the process/proto templates (relpath -> content).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.controller.models import InitResult


def init_workspace(path: Path, *, commit: bool = True) -> InitResult:
    raise NotImplementedError("PLAN §8.1")


def new_process_files(pid: str, *, goal: str | None) -> dict[str, str]:
    raise NotImplementedError("PLAN §8.1")

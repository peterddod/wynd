"""Importing step packages as `wynd_steps.<step_module_name(step_id)>` (PLAN §3.6; `$DRAFTS/02 §5.2`)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.runtime.step import Step


def mount_step_package(step_id: str, package_dir: str | Path) -> str:
    """Register `package_dir` as package `wynd_steps.<step_module_name(step_id)>`; return its dotted name.
    Idempotent."""
    raise NotImplementedError("PLAN §3.6")


def load_step_class(step_id: str, entrypoint: str, package_dir: str | Path | None) -> type[Step]:
    """Import `"<module>:<Class>"` from the step package (mounted when `package_dir` is given, installed otherwise)."""
    raise NotImplementedError("PLAN §3.6")

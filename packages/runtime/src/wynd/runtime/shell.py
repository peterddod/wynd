"""ShellStep execution (PLAN §5.2): argv from `command()`, run in the workspace with the venv's bin dir first on
PATH and `WYND_INPUT` set; the exit code maps through `exit_codes`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.runtime.step import ShellStep


@dataclass(frozen=True)
class ShellResult:
    returncode: int
    stdout: str
    stderr: str


def run_shell(step: ShellStep, input: Any) -> Any:
    raise NotImplementedError("PLAN §5.2")

"""ShellStep execution (PLAN §5.2): argv from `command()`, run in the workspace with the venv's bin dir first on
PATH and `WYND_INPUT` set; the exit code maps through `exit_codes`."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from wynd.runtime.errors import StepFailure

if TYPE_CHECKING:
    from wynd.runtime.step import ShellStep

TAIL = 2000


@dataclass(frozen=True)
class ShellResult:
    returncode: int
    stdout: str
    stderr: str


def run_shell(step: ShellStep, input: Any) -> Any:
    argv = step.command(input)
    if isinstance(argv, (str, bytes)):
        raise TypeError(f"{type(step).__name__}.command() must return an argv list, never a shell string")
    data = input.model_dump(mode="json") if isinstance(input, BaseModel) else input
    # The worker is the venv's python, not an activated venv: its bin dir first on PATH makes the console scripts
    # of the step's own dependencies runnable (SPEC §3.2 "the step's environment").
    path = f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"
    env = {**os.environ, "WYND_INPUT": json.dumps(data, ensure_ascii=False, default=str), "PATH": path}
    done = subprocess.run(
        [str(arg) for arg in argv],
        cwd=step.runtime.workspace,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=env,
    )
    rc, stdout, stderr = done.returncode, done.stdout, done.stderr
    codes = step.exit_codes
    exit = codes.get(rc, codes.get(str(rc), codes.get("*", "error")))
    if exit == "error":
        raise StepFailure(
            "shell_exit",
            f"exit code {rc}: {stderr[-TAIL:]}",
            partial_outputs={"returncode": rc, "stdout": stdout[-TAIL:], "stderr": stderr[-TAIL:]},
        )
    return step.outputs(exit, ShellResult(rc, stdout, stderr))

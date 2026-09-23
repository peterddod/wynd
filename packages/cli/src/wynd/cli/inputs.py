"""Run inputs and compile answers from the command line (PLAN §9, §3.22; `$DRAFTS/06 §9.1` "Inputs", §10.6). Stub;
CLI-M1.

Inputs merge file, then `--inputs`, then `--input K=V` (values parsed with `yaml.safe_load`); `path`-typed fields (per
`ctl.processes.interface(pid)`, `format: "path"`) are made absolute against the cwd. Answers: a YAML mapping of
question id -> answer text, overridden by `--answer QID=TEXT` pairs.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.controller import Controller


def parse_inputs(
    ctl: Controller,
    pid: str,
    *,
    pairs: Sequence[str] = (),
    inputs_json: str | None = None,
    inputs_file: str | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    raise NotImplementedError("PLAN §9")


def parse_answers(answers_file: Path | None = None, pairs: Sequence[str] = ()) -> dict[str, str]:
    raise NotImplementedError("PLAN §9")

"""One attempt: write files, static checks, interface check in the step venv, the package's test suite, and
per-example classification from the `WYND-EXPECT` lines (`$DRAFTS/05 §7.6`, PLAN §7 item 6).

Attempt tests run with `WYND_CASSETTE_MODE=record|replay`, `WYND_CASSETTE_RECORD_DIR=<scratch>/attempts/<node>/<n>/
cassettes`, `WYND_DEFAULT_PROVIDER=<root provider>` and `WYND_EVENTS_FILE=<scratch>/…/events.jsonl`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wynd.compiler.pipeline import CompileEnv
    from wynd.spec.interface import Interface
    from wynd.spec.lockfiles import StepKind


class FailureInfo:
    """One failing case as shown to the revise prompt ($DRAFTS/05 §13.4 "Failures section"). Stub: lands with
    CMP-C."""


@dataclass
class AttemptResult:
    n: int
    kind: StepKind
    tier: str | None
    cases: dict[int, Literal["handled", "deferred", "wrong"]]     # example number -> class
    failures: list[FailureInfo]                                   # for the revise prompt
    static_errors: list[str]
    all_passed: bool
    handled: int
    deferred: int
    wrong: int
    cassettes: Path | None                                        # snapshot of this attempt's recordings
    events_file: Path | None
    duration_ms: int


def run_attempt(env: CompileEnv, pkg_dir: Path, files: dict[str, str], *, kind: StepKind, entrypoint: str,
                requirements: list[str], expected: Interface, mode: Literal["replay", "record"], n: int) -> AttemptResult:
    raise NotImplementedError("PLAN §7")

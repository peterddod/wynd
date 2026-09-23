"""The test runner: step suites, process examples and test records (PLAN §3.20; owner PROC-ENV).

`run_tests` = `$DRAFTS/04 §7` (plan, per-package interface-drift check via `python -m wynd.runtime.describe`, hermetic
pytest per compiled package in its venv, exit code 5 = "no tests collected" = failure) + the process examples of
every closure process (root first). Results are recorded only when `commit` and `runs` are given and the closure is
clean. The result models below are the W0-complete contract.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from wynd.runtime.storage.base import RunRegistry

    from .jobs import JobContext, JobOutcome
    from .workspace import Workspace


class TestCase(BaseModel):
    __test__ = False                      # not a pytest test class
    name: str
    outcome: Literal["passed", "failed", "error", "skipped"]
    message: str | None = None
    duration_ms: int


class SuiteResult(BaseModel):
    subject: str
    hash: str
    passed: bool
    counts: dict[str, int]
    cases: list[TestCase]
    problem: str | None = None


class TestReport(BaseModel):
    __test__ = False                      # not a pytest test class
    process: str
    commit: str | None
    process_hash: str
    mode: Literal["replay", "live"]
    passed: bool
    suites: list[SuiteResult]
    started_at: datetime
    finished_at: datetime
    recorded: bool = False


def run_tests(
    ws: Workspace,
    pid: str,
    *,
    mode: Literal["replay", "live"],
    commit: str | None,
    runs: RunRegistry | None,
    venv_root: Path,
    scratch: Path,
    env: Mapping[str, str] | None = None,
    log: Callable[[str], None] | None = None,
) -> TestReport:
    raise NotImplementedError("PLAN §3.20 run_tests")


def run_step_suite(
    pkg_dir: Path,
    *,
    python: Path,
    mode: Literal["replay", "record"],
    env: Mapping[str, str],
    junit: Path,
    basetemp: Path,
    record_dir: Path | None = None,
    timeout_s: int = 1800,
) -> SuiteResult:
    raise NotImplementedError("PLAN §3.20 run_step_suite")


def run_process_examples(
    ws: Workspace,
    pid: str,
    *,
    mode: Literal["replay", "record"],
    env: Mapping[str, str],
    scratch: Path,
    venv_root: Path,
    record_root: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> SuiteResult:
    raise NotImplementedError("PLAN §3.20 run_process_examples")


def tests_status(runs: RunRegistry, ws: Workspace, pid: str, commit: str) -> dict[str, Any]:
    raise NotImplementedError("PLAN §3.20 tests_status")


def run_test_live_job(ctx: JobContext) -> JobOutcome:
    raise NotImplementedError("PLAN §3.20 run_test_live_job")

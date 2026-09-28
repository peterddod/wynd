"""Fixtures for the optimise tests (owner OPT-PROC).

`fixtures/*.jsonl` are synthetic §3.13 traces of the fixture workspace's `intake` process (`fixtures/ws`: read ->
extract -> agentic check `extract.done[save]` -> classify, else the child process `review` = judge + its own check):

- `demote`: 25 clean live runs, extract and classify at standard, the check at cheap.
- `promote`: 20 runs at cheap; runs 1–5 end extract in `error` (output_validation, 3 invalid calls, no `model` on the
  `step.end`, as the runtime emits it), run 6 needs one validation retry.
- `hysteresis`: 20 clean runs at standard, then 6 runs whose extract fails validation at cheap.
- `replay_only`: `demote` replayed from cassettes.
- `fast`: 6 runs with slow extract calls (2.6–3.1 s), runs over 10 s and 2 s of executor overhead per transition.
- `edge`: 25 runs; the check fails validation in runs 1–2 (no usage/attempts on the `edge.check`), says no in runs
  3–10 (the else branch runs the child process) and yes in 11–25.
- `nested`: one run through the child process.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from wynd.process.optimise import ProcessStats, collect_stats
from wynd.process.workspace import load_workspace

FIXTURES = Path(__file__).parent / "fixtures"


def _read_runs(name: str) -> tuple[list[str], Callable[[str], list[dict]]]:
    """(run ids in file order, read_events) of `fixtures/<name>.jsonl`."""
    by_run: dict[str, list[dict]] = {}
    for line in (FIXTURES / f"{name}.jsonl").read_text().splitlines():
        event = json.loads(line)
        by_run.setdefault(event["run_id"], []).append(event)
    return list(by_run), lambda run_id: by_run.get(run_id, [])


@pytest.fixture
def read_runs() -> Callable[[str], tuple[list[str], Callable[[str], list[dict]]]]:
    return _read_runs


@pytest.fixture
def stats_of() -> Callable[..., ProcessStats]:
    def stats_of(name: str, run_ids: list[str] | None = None) -> ProcessStats:
        ids, read = _read_runs(name)
        return collect_stats("intake", ids if run_ids is None else run_ids, read)

    return stats_of


@pytest.fixture
def intake(make_repo):
    """The fixture workspace's `intake` process, loaded from a git checkout (so `edges_lock_of` reads its tree)."""
    return load_workspace(make_repo(source=FIXTURES / "ws")).load_process("intake")

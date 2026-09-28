"""M5 acceptance of `wynd optimise --apply` on the dogfood (PLAN §12 item 5, §14 OPT-CTL; `$DRAFTS/08 §3.9`), live.

In a temporary git copy of the sample with `extract` locked at `standard`, the TraceSink is seeded with 20 clean live
runs of the process (extract at claude-code/`standard`), so the report recommends D1: demote `extract` to `cheap`.
`wynd optimise --apply --unit extract` then runs the optimise job for real: extract's own tests re-recorded live at
`cheap`, the process examples re-recorded live, one commit, fast-forwarded into `main`. Afterwards the lock says
`cheap`, extract's recordings are fresh `cheap` ones and `wynd test` passes in replay.

The runs are seeded (synthetic `step.start`/`model.call`/`step.end`/`run.end` events marked live) rather than
executed: twenty real runs at `standard` would only re-test what the offline report tests cover, for minutes and
money; the job, the part that talks to the model, is real. Runs only with `WYND_LIVE=1`.
"""

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wynd.cli.main import app
from wynd.controller import Controller

WS = Path(__file__).resolve().parents[1]
PID = "process_supplier_invoice"
PDIR = f"processes/{PID}"
EXTRACT = f"{PDIR}/steps/extract_invoice_fields"
LOCK = f"{EXTRACT}/step.lock.yaml"
RUNS = 20
COPY_IGNORE = shutil.ignore_patterns(".wynd", "__pycache__", ".pytest_cache", ".env")
GIT_IDENTITY = ["-c", "user.name=Wynd Test", "-c", "user.email=test@wynd.invalid", "-c", "commit.gpgsign=false"]
USAGE = {"input_tokens": 1200, "output_tokens": 90, "cache_read_tokens": 0, "cache_write_tokens": 0,
         "cost_usd": 0.004, "latency_ms": 4200.0, "calls": 1}
MODEL = {"provider": "claude-code", "model_id": "sonnet", "tier": "standard", "thinking": "low"}

pytestmark = pytest.mark.live


def git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ws), *GIT_IDENTITY, *args], check=True, capture_output=True,
                          text=True).stdout


def wynd(ws: Path, *args: str) -> str:
    result = CliRunner().invoke(app, ["-C", str(ws), *args])
    assert result.exit_code == 0, (f"wynd {' '.join(args)} exited {result.exit_code}\n--- stdout\n{result.stdout}\n"
                                   f"--- stderr\n{result.stderr}\n{result.exception!r}")
    return result.stdout


@pytest.fixture
def workspace(tmp_path, shared_venvs, monkeypatch) -> Path:
    """The sample in a fresh repository on `main`, `extract` locked at `standard` (one commit)."""
    ws = tmp_path / "invoices"
    shutil.copytree(WS, ws, ignore=COPY_IGNORE)
    lock = ws / LOCK
    text, count = re.subn(r"^tier: cheap$", "tier: standard", lock.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert count == 1
    lock.write_text(text, encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "extract at standard"]):
        git(ws, *args)
    (ws / ".wynd").mkdir()
    (ws / ".wynd" / "venvs").symlink_to(shared_venvs, target_is_directory=True)
    monkeypatch.setenv("UV_OFFLINE", "1")
    for name in ("RECORDS_DIR", "REVIEW_DIR", "ESCALATIONS_DIR"):
        monkeypatch.setenv(name, str(tmp_path / "out" / name.removesuffix("_DIR").lower()))
    monkeypatch.chdir(ws)
    return ws


def seed_runs(ws: Path) -> None:
    """`RUNS` clean runs whose `extract` made one live call at claude-code/standard."""
    stores = Controller.open(ws, load_dotenv=False).ctx.stores
    start = datetime(2026, 9, 1, tzinfo=UTC)
    for i in range(1, RUNS + 1):
        run_id, at = f"run_seed_{i:03d}", (start + timedelta(minutes=i)).isoformat()
        stores.runs.create({"id": run_id, "kind": "run", "process": PID, "status": "succeeded", "created_at": at})
        events = [
            {"type": "step.start", "step": "extract", "name": "extract", "process": PID, "span": 1, "parent": None,
             "id": f"{PID}#extract_invoice_fields", "kind": "agentic", "run": 1},
            {"type": "model.call", "step": "extract", "span": 1, "parent": None, "provider": "claude-code",
             "tier": "standard", "attempt": 1, "usage": USAGE, "cassette": "live"},
            {"type": "step.end", "step": "extract", "span": 1, "parent": None, "run": 1, "kind": "agentic",
             "exit": "done", "attempts": 1, "validation_failures": 0, "timings": {"duration_ms": 4600.0},
             "usage": USAGE, "model": MODEL, "replayed": False},
            {"type": "run.end", "exit": "done", "status": "succeeded", "duration_ms": 9000.0},
        ]
        for seq, event in enumerate(events, start=1):
            stores.traces.write({"v": 1, "seq": seq, "ts": at, "run_id": run_id, **event})
        stores.traces.close(run_id)


def recordings(ws: Path) -> dict[str, dict]:
    return {p.name: json.loads(p.read_text()) for p in sorted((ws / EXTRACT / "cassettes").glob("*.json"))}


def test_optimise_apply_demotes_extract_live(workspace):
    ws = workspace
    seed_runs(ws)
    report = wynd(ws, "optimise", PID)
    row = next(line for line in report.splitlines() if line.startswith("extract "))
    assert "standard" in row and row.endswith("demote -> cheap (D1)"), report

    before = recordings(ws)
    base = git(ws, "rev-parse", "HEAD").strip()
    out = wynd(ws, "optimise", PID, "--apply", "--unit", "extract")
    head = git(ws, "rev-parse", "HEAD").strip()
    assert f"fast-forwarded main to {head[:7]}" in out, out
    assert git(ws, "rev-parse", f"{head}~1").strip() == base
    assert git(ws, "log", "-1", "--format=%s").strip() == f"wynd optimise: {PID}"
    assert re.search(r"^extract: standard -> cheap \(D1: 0 failures", git(ws, "log", "-1", "--format=%B"), re.M)
    changed = git(ws, "diff", "--name-only", base, head).splitlines()
    assert LOCK in changed and any(p.startswith(f"{EXTRACT}/cassettes/") for p in changed)
    assert any(p.startswith(f"{PDIR}/cassettes/") for p in changed)                  # process examples re-recorded

    assert re.search(r"^tier: cheap$", (ws / LOCK).read_text(encoding="utf-8"), re.M)
    after = recordings(ws)
    newest_before = max(entry["recorded_at"] for entry in before.values())
    # the committed recordings are already `cheap`, so fresh ones keep their keys (PLAN §3.16); they are newer
    assert after and all(entry["recorded_at"] > newest_before for entry in after.values())
    assert {entry["tier"] for entry in after.values()} == {"cheap"}
    assert git(ws, "status", "--porcelain", "--", ".", ":(exclude).wynd") == ""

    assert "passed" in wynd(ws, "test", PID).splitlines()

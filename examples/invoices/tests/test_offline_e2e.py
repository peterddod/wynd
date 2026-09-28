"""The dogfood end to end, offline and deterministic (PLAN §12 item 5, M1-INT): CLI → controller → `run_local` →
venv workers → executor, with the `fake` provider standing in for Claude.

The sample workspace is copied into a temporary git repository, its `process.yaml` switched to `provider: fake`, and
`WYND_FAKE_PROVIDER_SCRIPT` pointed at `tests/fixtures/fake_provider.json` (the records the live model is expected
to return). Every process example then runs through `wynd run --local --json` and must end with the example's exit
and outputs, take the expected step path and leave the expected files behind. Step venvs are created offline
(`UV_OFFLINE=1`) under the session's shared venv root.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wynd.cli.main import app
from wynd.spec import load_process
from wynd.spec.hashing import jsonable

WS = Path(__file__).resolve().parents[1]
PID = "process_supplier_invoice"
PROCESS_REL = Path("processes") / PID
FAKE_SCRIPT = WS / "tests" / "fixtures" / "fake_provider.json"
EXAMPLES = load_process(WS / PROCESS_REL / "process.yaml").examples
COPY_IGNORE = shutil.ignore_patterns(".wynd", "__pycache__", ".pytest_cache", ".env", "cassettes")
GIT_IDENTITY = ["-c", "user.name=Wynd Test", "-c", "user.email=test@wynd.invalid", "-c", "commit.gpgsign=false"]
OUT_DIRS = ("records", "review", "escalations")

# (step, exit) of every step.end, in order, per example (1-based). The M2 image-parity test compares the same pairs.
SEQUENCES = {
    1: [("read", "done"), ("extract", "done"), ("validate", "done"), ("save", "done")],
    2: [("read", "done"), ("extract", "done"), ("validate", "done"), ("save", "done")],
    3: [("read", "done"), ("extract", "done"), ("validate", "done"), ("fix", "done"), ("validate", "done"),
        ("save", "done")],
    4: [("read", "done"), ("extract", "not_an_invoice")],
    5: [("read", "done"), ("extract", "done"), ("validate", "done"), ("escalate", "done")],
    6: [("read", "done"), ("extract", "done"), ("validate", "done"), ("escalate", "done")],
}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *GIT_IDENTITY, *args], check=True, capture_output=True,
                          text=True).stdout


def wynd(ws: Path, *args: str, code: int = 0) -> str:
    result = CliRunner().invoke(app, ["-C", str(ws), *args])
    if result.exit_code != code:
        raise AssertionError(f"wynd {' '.join(args)} exited {result.exit_code}, expected {code}\n"
                             f"--- stdout\n{result.stdout}\n--- stderr\n{result.stderr}\n{result.exception!r}")
    return result.stdout


@pytest.fixture(scope="module")
def workspace(tmp_path_factory, shared_venvs) -> Path:
    ws = tmp_path_factory.mktemp("offline-e2e") / "invoices"
    shutil.copytree(WS, ws, ignore=COPY_IGNORE)
    process_yaml = ws / PROCESS_REL / "process.yaml"
    text, count = re.subn(r"^provider: .*$", "provider: fake", process_yaml.read_text(encoding="utf-8"),
                          flags=re.MULTILINE)
    assert count == 1
    process_yaml.write_text(text, encoding="utf-8")
    git(ws, "init", "-q", "-b", "main")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", "sample workspace, fake provider")
    (ws / ".wynd").mkdir()
    (ws / ".wynd" / "venvs").symlink_to(shared_venvs, target_is_directory=True)
    return ws


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setenv("UV_OFFLINE", "1")
    monkeypatch.setenv("WYND_FAKE_PROVIDER_SCRIPT", str(FAKE_SCRIPT))


def example_env(n: int, tmp: Path) -> dict[str, str]:
    return {k: v.replace("{tmp}", str(tmp)) for k, v in EXAMPLES[n - 1].env.items()}


def run_example(ws: Path, n: int, tmp: Path, monkeypatch) -> dict:
    """`wynd run --local --json` of example `n` from the process dir, so its relative `path` inputs resolve against
    the process dir exactly as `wynd test` resolves them."""
    for key, value in example_env(n, tmp).items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(ws / PROCESS_REL)
    return json.loads(wynd(ws, "run", PID, "--local", "--inputs", json.dumps(EXAMPLES[n - 1].inputs), "--json"))


def step_ends(ws: Path, run_id: str) -> list[tuple[str, str]]:
    events = json.loads(wynd(ws, "trace", run_id, "--json"))["items"]
    return [(e["step"], e["exit"]) for e in events if e["type"] == "step.end"]


def written(tmp: Path) -> dict[str, list[str]]:
    return {d: sorted(p.name for p in (tmp / d).glob("*")) for d in OUT_DIRS if (tmp / d).exists()}


def test_every_example_has_an_expected_step_sequence():
    assert set(SEQUENCES) == set(range(1, len(EXAMPLES) + 1))


def test_validate_reports_only_the_two_cycle_infos(workspace):
    report = json.loads(wynd(workspace, "validate", PID, "--json"))
    assert report["ok"] is True
    assert [(i["severity"], i["code"]) for i in report["issues"]] == [("info", "I201"), ("info", "I201")]
    messages = " ".join(i["message"] for i in report["issues"] if i["code"] == "I201")
    assert "'validate.done[fix]'" in messages and "'fix.done[0]'" in messages


@pytest.mark.parametrize("n", range(1, len(EXAMPLES) + 1))
def test_example_runs_end_to_end(workspace, offline, tmp_path, monkeypatch, n):
    example = EXAMPLES[n - 1]
    run = run_example(workspace, n, tmp_path, monkeypatch)
    assert run["status"] == "succeeded"
    assert run["mode"] == "local"
    assert run["error"] is None
    assert run["exit"] == example.exit
    assert run["outputs"] == jsonable(example.outputs)
    assert step_ends(workspace, run["id"]) == SEQUENCES[n]


def test_records_land_in_records_dir_below_the_approval_threshold(workspace, offline, tmp_path, monkeypatch):
    run = run_example(workspace, 1, tmp_path, monkeypatch)
    assert written(tmp_path) == {"records": ["acme-supplies-ltd__INV-1042.json"]}
    saved = json.loads((tmp_path / "records" / "acme-supplies-ltd__INV-1042.json").read_text(encoding="utf-8"))
    assert saved == run["outputs"]["record"]


def test_records_over_ten_thousand_go_to_review(workspace, offline, tmp_path, monkeypatch):
    run_example(workspace, 2, tmp_path, monkeypatch)
    assert written(tmp_path) == {"review": ["globex-corporation__GX-77810.json"]}


def test_a_fixable_currency_is_fixed_once_and_the_tree_shows_the_loop(workspace, offline, tmp_path, monkeypatch):
    run = run_example(workspace, 3, tmp_path, monkeypatch)
    header, *lines = wynd(workspace, "trace", run["id"]).splitlines()
    assert header.startswith(f"run {run['id']}  {PID}  local  exit=done")
    assert all(line.startswith(("├─ ", "└─ ", "│  ")) for line in lines)
    nodes = [line[3:].split("  ")[0] for line in lines if line.startswith(("├─ ", "└─ "))]
    assert nodes == ["read", "extract", "validate", "fix", "validate #2", "save"]
    assert "│  → validate.done[fix] → fix" in lines
    assert written(tmp_path) == {"records": ["initech-canada-inc__IC-5521.json"]}


def test_not_an_invoice_writes_nothing(workspace, offline, tmp_path, monkeypatch):
    run_example(workspace, 4, tmp_path, monkeypatch)
    assert written(tmp_path) == {}


def test_a_credit_note_is_escalated_with_a_ticket(workspace, offline, tmp_path, monkeypatch):
    run = run_example(workspace, 5, tmp_path, monkeypatch)
    assert written(tmp_path) == {"escalations": [f"{run['id']}.json"]}
    ticket = json.loads((tmp_path / "escalations" / f"{run['id']}.json").read_text(encoding="utf-8"))
    assert ticket["run_id"] == run["id"]
    assert [e["code"] for e in ticket["errors"]] == ["non_positive_total"]
    assert ticket["fields"]["invoice_number"] == "CN-311"


def test_a_pro_forma_fails_the_agentic_check_and_is_escalated(workspace, offline, tmp_path, monkeypatch):
    run = run_example(workspace, 6, tmp_path, monkeypatch)
    assert written(tmp_path) == {"escalations": [f"{run['id']}.json"]}
    ticket = json.loads((tmp_path / "escalations" / f"{run['id']}.json").read_text(encoding="utf-8"))
    assert ticket["errors"] == []
    assert ticket["reasons"] == [f"routed to review by the process; see `wynd trace {run['id']}`"]
    events = json.loads(wynd(workspace, "trace", run["id"], "--json"))["items"]
    checks = [(e["branch_key"], e["take"]) for e in events if e["type"] == "edge.check"]
    assert checks == [("validate.done[save]", False)]

"""Live smoke test of the dogfood (PLAN §12 item 5, M1-INT acceptance 5): `wynd run --local` of example 1 with the
real default provider (`claude-code`, the user's Claude login or `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY`) exits
`done`, saves the record and traces the model call. Runs only with `WYND_LIVE=1`.

The workspace is a temporary git copy of the sample (its step venvs under the session's shared venv root, created
offline from the uv cache); the command is the M1 acceptance one, run from the workspace root.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wynd.cli.main import app

WS = Path(__file__).resolve().parents[1]
PID = "process_supplier_invoice"
PDF = f"processes/{PID}/examples/acme_inv_1042.pdf"
COPY_IGNORE = shutil.ignore_patterns(".wynd", "__pycache__", ".pytest_cache", ".env", "cassettes")
GIT_IDENTITY = ["-c", "user.name=Wynd Test", "-c", "user.email=test@wynd.invalid", "-c", "commit.gpgsign=false"]

pytestmark = pytest.mark.live


def wynd(ws: Path, *args: str) -> str:
    result = CliRunner().invoke(app, ["-C", str(ws), *args])
    assert result.exit_code == 0, (f"wynd {' '.join(args)} exited {result.exit_code}\n--- stdout\n{result.stdout}\n"
                                   f"--- stderr\n{result.stderr}\n{result.exception!r}")
    return result.stdout


@pytest.fixture
def workspace(tmp_path, shared_venvs, monkeypatch) -> Path:
    ws = tmp_path / "invoices"
    shutil.copytree(WS, ws, ignore=COPY_IGNORE)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "sample workspace"]):
        subprocess.run(["git", "-C", str(ws), *GIT_IDENTITY, *args], check=True, capture_output=True)
    (ws / ".wynd").mkdir()
    (ws / ".wynd" / "venvs").symlink_to(shared_venvs, target_is_directory=True)
    monkeypatch.setenv("UV_OFFLINE", "1")
    for name in ("RECORDS_DIR", "REVIEW_DIR", "ESCALATIONS_DIR"):
        monkeypatch.setenv(name, str(tmp_path / "out" / name.removesuffix("_DIR").lower()))
    monkeypatch.chdir(ws)
    return ws


def test_example_1_runs_live_and_exits_done(workspace, tmp_path):
    run = json.loads(wynd(workspace, "run", PID, "--local", "--input", f"pdf_path={PDF}", "--json"))
    assert (run["status"], run["exit"], run["error"]) == ("succeeded", "done", None)
    assert run["outputs"]["record"]["invoice_number"] == "INV-1042"
    assert [p.name for p in (tmp_path / "out" / "records").iterdir()] == ["acme-supplies-ltd__INV-1042.json"]

    events = json.loads(wynd(workspace, "trace", run["id"], "--json"))["items"]
    calls = [e for e in events if e["type"] == "model.call"]
    assert calls and all(c["provider"] == "claude-code" and c["cassette"] == "live" for c in calls)
    extract = next(e for e in events if e["type"] == "step.end" and e["step"] == "extract")
    model = extract["model"]
    assert (model["provider"], model["tier"]) == ("claude-code", "cheap") and "haiku" in model["model_id"]
    tree = wynd(workspace, "trace", run["id"])
    assert any(line.startswith("├─ extract") and f" {model['model_id']} " in line for line in tree.splitlines())

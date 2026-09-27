"""M3 acceptance (SPEC §12 M3 "`wynd compile` produces the M1 process from proto-steps alone"; `$DRAFTS/05 §16.4`,
PLAN §14 Wave 8): in a temporary git copy of the sample without its compiled steps and cassettes, `wynd compile
--accept-proposals` exits 0 and fast-forwards, the step kinds are the expected ones, `wynd test` passes in replay with
live calls made impossible, a second compile changes nothing, and one added example recompiles only that step.

Runs only with `WYND_LIVE=1` (the compiler and the agentic steps' recordings use the real `claude-code` provider).
Prints the compile's wall time and reported cost.
"""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from wynd.cli.main import app
from wynd.spec.hashing import proto_hash
from wynd.spec.lockfiles import StepLock
from wynd.spec.proto_step import ProtoStep
from wynd.spec.yamlio import parse_model

WS = Path(__file__).resolve().parents[1]
PID = "process_supplier_invoice"
PDIR = f"processes/{PID}"
COPY_IGNORE = shutil.ignore_patterns(".wynd", "__pycache__", ".pytest_cache", ".env", "cassettes", "steps", "tests")
GIT_IDENTITY = ["-c", "user.name=Wynd Test", "-c", "user.email=test@wynd.invalid", "-c", "commit.gpgsign=false"]
DETERMINISTIC = ("read_pdf", "validate_fields", "save_record", "escalate_to_human")
NEW_EXAMPLE = """\
  - inputs: { fields: { supplier: " Umbrella   Corp ", invoice_number: um-9, total: 55.5, currency: eur, due_date: 2027-01-31 } }
    outputs:
      valid: true
      fixable: false
      errors: []
      fields: { supplier: Umbrella Corp, invoice_number: UM-9, total: 55.5, currency: EUR, due_date: 2027-01-31 }
      record: { key: umbrella-corp__UM-9, supplier: Umbrella Corp, invoice_number: UM-9, total: 55.5, currency: EUR, due_date: 2027-01-31 }
    exit: done
"""

pytestmark = pytest.mark.live


def git(ws: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(ws), *GIT_IDENTITY, *args], check=True, capture_output=True,
                          text=True).stdout


def wynd(ws: Path, *args: str) -> str:
    result = CliRunner().invoke(app, ["-C", str(ws), *args])
    assert result.exit_code == 0, (f"wynd {' '.join(args)} exited {result.exit_code}\n--- stdout\n{result.stdout}\n"
                                   f"--- stderr\n{result.stderr}\n{result.exception!r}")
    return result.stdout


def compile_job(ws: Path) -> dict:
    """The newest compile job of the process (the CLI's `jobs list --json`)."""
    return json.loads(wynd(ws, "jobs", "list", "--process", PID, "--kind", "compile", "--limit", "1", "--json"))[0]


def locks(ws: Path) -> dict[str, StepLock]:
    return {p.parent.name: parse_model(p.read_text(), StepLock, str(p))
            for p in sorted((ws / PDIR / "steps").glob("*/step.lock.yaml"))}


@pytest.fixture
def workspace(tmp_path, shared_venvs, monkeypatch) -> Path:
    ws = tmp_path / "invoices"
    shutil.copytree(WS, ws, ignore=COPY_IGNORE)
    assert not (ws / PDIR / "steps").exists() and (ws / PDIR / "proto").is_dir()
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "proto-steps only"]):
        git(ws, *args)
    (ws / ".wynd").mkdir()
    (ws / ".wynd" / "venvs").symlink_to(shared_venvs, target_is_directory=True)
    for name in ("RECORDS_DIR", "REVIEW_DIR", "ESCALATIONS_DIR"):
        monkeypatch.setenv(name, str(tmp_path / "out" / name.removesuffix("_DIR").lower()))
    monkeypatch.chdir(ws)
    return ws


def test_compile_produces_the_m1_process_from_proto_steps(workspace, tmp_path, monkeypatch):
    ws = workspace
    wynd(ws, "validate", PID)

    base = git(ws, "rev-parse", "HEAD").strip()
    started = time.monotonic()
    out = wynd(ws, "compile", PID, "--accept-proposals")
    wall_s = time.monotonic() - started
    job = compile_job(ws)
    assert job["status"] == "succeeded" and re.search(rf"wynd/compile/{PID}/{job['id']}", out)
    head = git(ws, "rev-parse", "HEAD").strip()
    assert head == job["result_commit"] and git(ws, "rev-parse", f"{head}~1").strip() == base    # fast-forwarded
    assert git(ws, "status", "--porcelain", "--", ".", ":(exclude).wynd") == ""
    assert git(ws, "log", "-1", "--format=%s").strip() == f"wynd compile: {PID}"
    usage = (job.get("report") or {}).get("usage", {}).get("total", {})
    print(f"\nM3 compile: wall {wall_s:.0f} s, compiler cost {usage.get('cost_usd')} USD, calls {usage.get('calls')}")

    compiled = locks(ws)
    for name, lock in compiled.items():
        assert lock.compiled, f"{name} has no compiled section"
        proto = ws / PDIR / "proto" / f"{name}.yaml"
        if proto.exists():
            assert lock.proto_hash == proto_hash(parse_model(proto.read_text(), ProtoStep, str(proto)))
        if lock.kind == "agentic":
            assert any((ws / PDIR / "steps" / name / "cassettes").rglob("*.json")), f"{name} has no cassettes"
    assert {name: compiled[name].kind for name in DETERMINISTIC} == dict.fromkeys(DETERMINISTIC, "deterministic")
    assert compiled["fix_fields"].kind == "agentic"
    match compiled["extract_invoice_fields"].kind:
        case "agentic":
            assert "extract_invoice_fields_agentic" not in compiled
        case "deterministic":                                          # split (rule 3)
            assert compiled["extract_invoice_fields_agentic"].kind == "agentic"
        case other:
            pytest.fail(f"extract_invoice_fields compiled as {other}")
    assert (ws / PDIR / "cassettes").is_dir()                          # process examples recorded

    events = tmp_path / "events.jsonl"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-invalid-replay-check")  # any live call would fail with 401
    monkeypatch.setenv("WYND_EVENTS_FILE", str(events))
    wynd(ws, "test", PID)
    calls = [e for e in map(json.loads, events.read_text().splitlines()) if e.get("type") == "model.call"]
    assert calls and {c["cassette"] for c in calls} == {"replay"}
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv("WYND_EVENTS_FILE")

    wynd(ws, "compile", PID, "--accept-proposals")                     # nothing changed: every step skipped
    again = compile_job(ws)
    assert again["id"] != job["id"] and again["status"] == "succeeded" and again["result_commit"] is None
    assert git(ws, "rev-parse", "HEAD").strip() == head
    assert {s["action"] for s in again["report"]["steps"]} == {"skipped"}

    proto = ws / PDIR / "proto" / "validate_fields.yaml"
    text = proto.read_text()
    proto.write_text(text.replace("env:\n", NEW_EXAMPLE + "env:\n", 1))
    assert len(yaml.safe_load(proto.read_text())["examples"]) == len(yaml.safe_load(text)["examples"]) + 1
    git(ws, "commit", "-q", "-am", "one more validate_fields example")
    before = git(ws, "rev-parse", "HEAD").strip()
    test_file = ws / PDIR / "steps" / "validate_fields" / "test_validate_fields.py"
    tests_before = test_file.read_text().count("def test_example_")
    wynd(ws, "compile", PID, "--accept-proposals")
    changed = git(ws, "diff", "--name-only", before, "HEAD").split()
    allowed = (f"{PDIR}/steps/validate_fields/", f"{PDIR}/proto/validate_fields.yaml", f"{PDIR}/cassettes/")
    assert changed and all(path.startswith(allowed) for path in changed), changed
    assert test_file.read_text().count("def test_example_") >= tests_before + 1
    report = compile_job(ws)["report"]
    assert [s["node"] for s in report["steps"] if s["action"] == "compiled"] == ["validate"]

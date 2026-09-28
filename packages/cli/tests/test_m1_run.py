"""`wynd run` and `wynd trace` (PLAN §9, §3.22, §3.13; `$DRAFTS/06 §9.2`) over a real controller running `p1`
(`upper` -> `count`, which writes to `RECORDS_DIR`) with venv workers, offline. Exit codes: 0 for `done` and for the
declared non-error exit `empty`, 1 for `$exit.error`, 2 for invalid inputs, 3 for the env gate, an unknown process,
a design-phase process and an image that is not built."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def records_dir(tmp_path) -> Path:
    return tmp_path / "records"


@pytest.fixture
def ctl(workspace, make_controller, use_controller, records_dir):
    return use_controller(make_controller(workspace, env={"RECORDS_DIR": str(records_dir)}))


def run_id_of(stdout: str) -> str:
    return next(line.split()[1] for line in stdout.splitlines() if line.startswith("run: "))


def test_run_prints_steps_then_the_exit_and_outputs(ctl, cli, records_dir):
    result = cli("run", "p1", "--input", "text=hello world")
    lines = result.stdout.splitlines()
    assert lines[0].split()[:2] == ["upper", "done"] and lines[0].split()[2].endswith("ms")
    assert "text=HELLO WORLD" in lines[0]
    assert lines[1].split()[:2] == ["count", "done"] and "words=2" in lines[1]
    run_id = run_id_of(result.stdout)
    assert lines[2:4] == [f"run: {run_id}", "exit: done"]
    assert json.loads("\n".join(lines[4:])) == {"words": 2, "path": str(records_dir / "count.txt")}
    assert (records_dir / "count.txt").read_text() == "HELLO WORLD"

    run = ctl.runs.get(run_id)
    assert (run.status, run.trigger, run.target.kind, run.inputs) == ("succeeded", "manual", "local",
                                                                     {"text": "hello world"})


def test_text_with_line_breaks_stays_on_its_step_line(ctl, cli):
    lines = cli("run", "p1", "--inputs", '{"text": "hello\\nworld"}').stdout.splitlines()
    assert lines[0].split()[:2] == ["upper", "done"] and 'text="HELLO\\nWORLD"' in lines[0]
    assert lines[1].split()[:2] == ["count", "done"]


def test_a_declared_non_error_exit_is_success(ctl, cli):
    result = cli("run", "p1", "--inputs", '{"text": "   "}')
    assert result.stdout.splitlines()[0].split()[:2] == ["upper", "empty"]
    assert "exit: empty" in result.stdout.splitlines()


def test_an_error_exit_exits_1_and_points_at_the_trace(ctl, cli, records_dir):
    records_dir.parent.mkdir(parents=True, exist_ok=True)
    records_dir.write_text("a file where the directory should be")
    result = cli("run", "p1", "--input", "text=hello", code=1)
    lines = result.stdout.splitlines()
    assert lines[1].split()[:2] == ["count", "error"]
    assert lines[2].startswith("  cause: ")
    run_id = run_id_of(result.stdout)
    assert f"run: {run_id}" in lines and "exit: error" in lines
    assert any(line.startswith("error: step_error at count: ") for line in lines)
    assert lines[-1] == f"trace: wynd trace {run_id}"


def test_json_prints_only_the_run(ctl, cli, records_dir):
    doc = json.loads(cli("run", "p1", "--input", "text=a b c", "--json").stdout)
    assert (doc["status"], doc["exit"], doc["outputs"]["words"], doc["trigger"]) == ("succeeded", "done", 3, "manual")

    shutil.rmtree(records_dir)
    records_dir.write_text("not a directory")
    failed = json.loads(cli("run", "p1", "--input", "text=x", "--json", code=1).stdout)
    assert (failed["status"], failed["error"]["cause"]) == ("failed", "step_error")


def test_full_prints_inputs_and_outputs_and_child_steps_are_indented(ctl, cli):
    lines = cli("run", "parent", "--input", "text=a b", "--full").stdout.splitlines()
    assert lines[0].startswith("  upper ") and lines[1] == '    in: {"text":"a b"}'
    assert lines[2] == '    out: {"text":"A B"}'
    assert lines[3].startswith("  count ")
    child = next(i for i, line in enumerate(lines) if line.startswith("child "))
    assert lines[child + 1] == '  in: {"text":"a b"}' and lines[child + 2].startswith('  out: {"words":2')


def test_inputs_merge_file_then_json_then_pairs(ctl, cli, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "inputs.yaml").write_text("text: from the file\n")
    result = cli("run", "p1", "--inputs-file", "inputs.yaml", "--json")
    assert json.loads(result.stdout)["outputs"]["words"] == 3
    result = cli("run", "p1", "--inputs-file", "inputs.yaml", "--inputs", '{"text": "json wins"}', "--json")
    assert json.loads(result.stdout)["inputs"] == {"text": "json wins"}
    result = cli("run", "p1", "--inputs", '{"text": "json"}', "--input", "text=pair wins", "--json")
    assert json.loads(result.stdout)["inputs"] == {"text": "pair wins"}
    result = cli("run", "p1", "--inputs-file", "-", "--json", input="text: from stdin\n")
    assert json.loads(result.stdout)["inputs"] == {"text": "from stdin"}


def test_path_inputs_reach_the_run_absolute(ctl, cli, tmp_path, monkeypatch):
    seen = []

    def spy(pid, inputs, *, target, on_event, trigger):
        seen.append((pid, inputs, target, trigger))
        return ctl.runs.get(run_id)

    run_id = run_id_of(cli("run", "p1", "--input", "text=x").stdout)
    monkeypatch.setattr(ctl.runs, "run", spy)
    monkeypatch.chdir(tmp_path)
    cli("run", "p3", "--input", "doc=docs/a.pdf", "--input", "pages=2", "--image", "--commit", "abc1234")
    ((pid, inputs, target, trigger),) = seen
    assert (pid, inputs, trigger) == ("p3", {"doc": str(tmp_path / "docs/a.pdf"), "pages": 2}, "manual")
    assert (target.kind, target.commit) == ("image", "abc1234")


@pytest.mark.parametrize(("args", "code", "message"), [
    (["run", "p1", "--input", "words=1"], 2, "error: invalid process inputs"),
    (["run", "p1", "--input", "no-equals-sign"], 2, "error: --input expects KEY=VALUE"),
    (["run", "p1", "--commit", "abc"], 2, "error: --commit applies to --image runs only"),
    (["run", "nope"], 3, "error: unknown process 'nope'"),
    (["run", "p2", "--input", "name=Ada"], 3, "design phase"),
    (["run", "p1", "--image", "--input", "text=x"], 3, "error: process 'p1' has no build at"),
])
def test_refusals(ctl, cli, args, code, message):
    result = cli(*args, code=code)
    assert message in result.stderr and result.stdout == ""
    assert ctl.runs.list() == []


def test_the_env_gate_exits_3_and_records_nothing(workspace, make_controller, use_controller, cli):
    ctl = use_controller(make_controller(workspace, env={"RECORDS_DIR": None}))
    result = cli("run", "p1", "--input", "text=x", code=3)
    assert result.stderr.splitlines() == [
        "error: process 'p1' needs env var(s) that are not set: RECORDS_DIR",
        "hint: set them in the environment or in the workspace .env file (see `wynd env check p1`)",
    ]
    doc = json.loads(cli("run", "p1", "--input", "text=x", "--json", code=3).stdout)
    assert doc["error"]["code"] == "env_missing" and doc["error"]["details"]["missing"] == ["RECORDS_DIR"]
    assert ctl.runs.list() == []


def test_a_usage_error_exits_2(ctl, cli):
    assert "Missing argument" in cli("run", code=2).stderr
    assert "No such option: --nope" in cli("run", "p1", "--nope", code=2).stderr


# --- trace ------------------------------------------------------------------------------------------------------------

def test_trace_renders_the_tree(ctl, cli):
    run_id = run_id_of(cli("run", "p1", "--input", "text=hello world").stdout)
    lines = cli("trace", run_id).stdout.splitlines()
    assert lines[0].startswith(f"run {run_id}  p1  local  exit=done  ")
    assert lines[1].startswith("├─ upper ") and "text=HELLO WORLD" in lines[1]
    assert lines[2].startswith("└─ count ") and "words=2" in lines[2]
    assert len(lines) == 3

    full = cli("trace", run_id, "--full").stdout
    assert '│  in: {"text":"hello world"}' in full

    events = json.loads(cli("trace", run_id, "--json").stdout)["items"]
    assert events[0]["type"] == "run.start" and events[-1]["type"] == "run.end"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))


def test_trace_follow_waits_for_the_end(ctl, cli):
    from wynd.controller.models import CreateRunRequest

    run = ctl.runs.start(CreateRunRequest(process_id="p1", inputs={"text": "one two"}))
    result = cli("trace", run.id, "--follow")
    assert result.stdout.splitlines()[0].startswith(f"run {run.id}  p1  local  exit=done")
    assert [line.split()[0] for line in result.stderr.splitlines()] == ["upper", "count"]


def test_trace_of_an_unknown_run(ctl, cli):
    assert "unknown run 'run_nope'" in cli("trace", "run_nope", code=3).stderr

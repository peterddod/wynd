"""`wynd.runtime.bake.main` (`$DRAFTS/04 §8.9`): a baked process runs with every worker on the current interpreter,
the extracted `site` as its only extra path, inputs from argv or stdin, the outputs JSON on stdout and the CLI exit
codes (0 declared exit, 1 `$exit.error`, 2 bad inputs, 3 env check failed)."""

from __future__ import annotations

import io
import json
import sys
import textwrap
from pathlib import Path

import pytest

from wynd.runtime.bake import main
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.fragments import EnvVar
from wynd.spec.lockfiles import StepLock, dump_lock
from wynd.spec.plan import PlanNode, PlanProcess, PlanStep, PlanVenv, RunPlan
from wynd.spec.process_doc import ProcessDoc
from wynd.spec.workspace import step_module_name
from wynd.spec.yamlio import parse_model

SID = "doubler#double"

DOUBLE_PY = '''
    """Double a number; negative numbers fail."""
    from pathlib import Path
    from typing import Literal

    from pydantic import BaseModel

    from wynd.runtime import DeterministicStep


    class Double(DeterministicStep):
        """Double x."""

        class Input(BaseModel):
            x: int
            note: Path | None = None

        class Done(BaseModel):
            exit: Literal["done"] = "done"
            y: int
            note: str | None = None

        Output = Done

        def run(self, input):
            if input.x < 0:
                raise ValueError("negative")
            return self.Done(y=2 * input.x, note=str(input.note) if input.note else None)
'''

PROCESS = """
    kind: process
    name: doubler
    entry: double
    inputs:
      x: integer
      note: path?
    outputs:
      y: integer
      note: string?
    steps:
      double: {use: ./steps/double}
    edges:
      - from: double.done
        to: $exit.done
        with: {y: steps.double.outputs.y, note: steps.double.outputs.note}
"""


@pytest.fixture
def baked(tmp_path: Path) -> tuple[Path, Path]:
    """An extracted bake: `<root>/site/wynd_steps/<module>/` and `<root>/_wynd_bake/plan.json` (venv "bake", no
    interpreter: `main` supplies it)."""
    root = tmp_path / "bake" / "0123456789abcdef"
    pkg = root / "site" / "wynd_steps" / step_module_name(SID)
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "double.py").write_text(textwrap.dedent(DOUBLE_PY).lstrip())
    doc = parse_model(textwrap.dedent(PROCESS), ProcessDoc)
    lock = StepLock(name="double", kind="deterministic", entrypoint="double:Double")
    plan = RunPlan(
        mode="image", root="doubler", commit="c" * 40, provider="fake", venv_root="",
        venvs=[PlanVenv(id="bake", steps=[SID])],
        steps={SID: PlanStep(id=SID, kind="deterministic", entrypoint=lock.entrypoint, venv="bake", package_dir=None,
                             lock=lock)},
        processes={"doubler": PlanProcess(id="doubler", dir=None, definition=doc,
                                          nodes={"double": PlanNode(step=SID)})},
    )
    (root / "_wynd_bake").mkdir()
    plan_path = root / "_wynd_bake" / "plan.json"
    plan_path.write_text(plan.model_dump_json())
    return plan_path, root / "site"


def test_runs_the_process_from_an_argv_json_and_prints_outputs(baked, capsys):
    plan_path, site = baked
    assert main(plan_path, site, ['{"x": 21}']) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["exit"] == "done"
    assert printed["outputs"] == {"y": 42, "note": None}
    assert printed["run_id"].startswith("run_")
    trace = site.parent / "data" / "traces" / f"{printed['run_id']}.jsonl"      # default data dir: next to site
    types = [json.loads(line)["type"] for line in trace.read_text().splitlines()]
    assert types[0] == "run.start" and types[-1] == "run.end"


def test_the_worker_is_this_interpreter_with_site_on_its_path(baked, capsys, monkeypatch):
    from wynd.runtime.worker import pool as pool_module

    seen = {}
    original = pool_module.WorkerPool

    class Spy(original):
        def __init__(self, plan, *, env=None, cwd=None):
            seen["python"] = [venv.python for venv in plan.venvs]
            seen["pythonpath"] = env["PYTHONPATH"]
            super().__init__(plan, env=env, cwd=cwd)

    monkeypatch.setattr(pool_module, "WorkerPool", Spy)
    plan_path, site = baked
    assert main(plan_path, site, ['{"x": 1}']) == 0
    assert seen == {"python": [sys.executable], "pythonpath": str(site)}


def test_reads_inputs_from_stdin(baked, capsys, monkeypatch):
    plan_path, site = baked
    monkeypatch.setattr("sys.stdin", io.StringIO('{"x": 5}'))
    assert main(plan_path, site, []) == 0
    assert json.loads(capsys.readouterr().out)["outputs"]["y"] == 10
    monkeypatch.setattr("sys.stdin", io.StringIO('{"x": 6}'))
    assert main(plan_path, site, ["-"]) == 0
    assert json.loads(capsys.readouterr().out)["outputs"]["y"] == 12


def test_relative_path_inputs_are_made_absolute_against_the_cwd(baked, capsys, monkeypatch, tmp_path):
    plan_path, site = baked
    monkeypatch.chdir(tmp_path)
    assert main(plan_path, site, ['{"x": 1, "note": "notes/a.txt"}']) == 0
    assert json.loads(capsys.readouterr().out)["outputs"]["note"] == str(tmp_path / "notes" / "a.txt")


def test_a_failing_step_ends_in_exit_error_with_code_1(baked, capsys):
    plan_path, site = baked
    assert main(plan_path, site, ['{"x": -1}']) == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed["exit"] == "error"
    assert printed["outputs"]["error"]["cause"] == "step_error"
    assert printed["outputs"]["error"]["step_error"]["cause"] == "exception"


@pytest.mark.parametrize("argv, message", [
    (['{"x": "many"}'], "invalid process inputs"),
    (["not json"], "inputs are not valid JSON"),
    (["[1]"], "inputs must be a JSON object"),
    (['{"x": 1}', "extra"], "expected at most one argument"),
])
def test_bad_inputs_exit_2_without_running(baked, capsys, argv, message):
    plan_path, site = baked
    assert main(plan_path, site, argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err
    assert not (site.parent / "data" / "traces").exists()


def test_a_failing_env_check_exits_3_before_running(baked, capsys, monkeypatch):
    plan_path, site = baked
    manifest = EnvManifest(process="doubler", vars=[EnvVar(name="DOUBLER_KEY", secret=True, used_by=["runtime"])])
    (plan_path.parent / "process.env.yaml").write_text(dump_lock(manifest))
    monkeypatch.delenv("DOUBLER_KEY", raising=False)
    assert main(plan_path, site, ['{"x": 1}']) == 3
    captured = capsys.readouterr()
    assert "E-ENV-MISSING" in captured.err and "DOUBLER_KEY" in captured.err
    assert captured.out == ""

    monkeypatch.setenv("DOUBLER_KEY", "set")
    assert main(plan_path, site, ['{"x": 1}']) == 0

"""The worker pool consuming a `RunPlan` (PLAN §3.11, §3.6, §5.3): `init` snapshots taken from the step locks, the
venv interpreter derived from `venv_root`, image-mode steps installed under `wynd_steps.<module name>`, a plan read
back from `process.lock.yaml`, and routing of steps to their own venv."""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest

from wynd.runtime.errors import StepDefinitionError
from wynd.runtime.interface import interface_of
from wynd.runtime.policy import ExecPolicy
from wynd.runtime.worker.loader import load_step_class
from wynd.runtime.worker.pool import WorkerPool
from wynd.runtime.worker.protocol import InitStep, RunStepParams, init_step
from wynd.spec.hashing import interface_hash
from wynd.spec.interface import Interface
from wynd.spec.lockfiles import (
    BaseChoice,
    ProcessLock,
    RetryPolicy,
    ShellLock,
    StepLock,
    dump_lock,
    load_process_lock,
)
from wynd.spec.plan import PlanStep, PlanVenv, RunPlan
from wynd.spec.workspace import step_module_name

EXTRACT = {
    "extract.py": '''
        """Sum invoice lines."""
        from typing import Literal

        from pydantic import BaseModel

        from wynd.runtime import DeterministicStep

        from .rules import total


        class Extract(DeterministicStep):
            """Sum the invoice lines."""

            class Input(BaseModel):
                lines: list[float]

            class Done(BaseModel):
                exit: Literal["done"] = "done"
                total: float

            class Empty(BaseModel):
                exit: Literal["empty"] = "empty"

            Output = Done | Empty

            def run(self, input):
                if not input.lines:
                    return self.Empty()
                return self.Done(total=total(input.lines))
    ''',
    "rules.py": '''
        def total(lines):
            return round(sum(lines), 2)
    ''',
}

COUNT = {
    "count.py": '''
        from typing import Literal

        from pydantic import BaseModel

        from wynd.runtime import ShellStep


        class Count(ShellStep):
            """Count matching lines of data.txt."""

            exit_codes = {0: "done", 1: "missing", "*": "error"}

            class Input(BaseModel):
                pattern: str

            class Found(BaseModel):
                exit: Literal["done"] = "done"
                stdout: str

            class Missing(BaseModel):
                exit: Literal["missing"] = "missing"
                returncode: int

            Output = Found | Missing

            def command(self, input):
                return ["grep", "-c", input.pattern, "data.txt"]
    ''',
}

SHELL_CODES = {0: "done", 1: "missing", "*": "error"}
STALE = Interface(input={"type": "object", "properties": {"other": {"type": "string"}}}, outputs={"done": {}})
DRIFT_MESSAGE = "interface snapshot drift for step {}: run wynd validate --sync-interfaces"


def write_package(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, source in files.items():
        (root / name).write_text(textwrap.dedent(source))
    return root


def true_interface(step_id: str, entrypoint: str, package_dir: Path) -> Interface:
    return interface_of(load_step_class(step_id, entrypoint, package_dir)).interface


def params(step_id: str, inputs: dict, workspace: Path, kind: str = "deterministic") -> RunStepParams:
    return RunStepParams(
        run_id="run-plan", step_path=step_id.rpartition("#")[2], step_run=1, step_id=step_id, inputs=inputs,
        workspace=str(workspace), policy=ExecPolicy(kind=kind, retries=RetryPolicy()),
    )


def ignore(event: dict) -> None:
    return None


@pytest.fixture
def steps_namespace():
    """Forget the `wynd_steps` packages mounted in-process to compute true interfaces."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name == "wynd_steps" or name.startswith("wynd_steps."):
            del sys.modules[name]


@pytest.fixture
def workspace(tmp_path) -> Path:
    workspace = tmp_path / "run-ws"
    workspace.mkdir()
    (workspace / "data.txt").write_text("apple\nbanana\napple\n")
    return workspace


def test_init_step_takes_every_snapshot_from_the_lock():
    iface = Interface(input={"type": "object"}, outputs={"done": {"type": "object"}})
    deterministic = PlanStep(
        id="p#extract", kind="deterministic", entrypoint="extract:Extract", venv="v", package_dir="/abs/extract",
        lock=StepLock(name="extract", kind="deterministic", entrypoint="extract:Extract", interface=iface),
    )
    assert init_step(deterministic) == InitStep(
        id="p#extract", entrypoint="extract:Extract", package_dir="/abs/extract", kind="deterministic",
        interface_hash=interface_hash(iface), context=[], exit_codes=None,
    )
    shell = PlanStep(
        id="p#count", kind="shell", entrypoint="count:Count", venv="v", package_dir=None,
        lock=StepLock(name="count", kind="shell", entrypoint="count:Count", shell=ShellLock(exit_codes=SHELL_CODES)),
    )
    assert init_step(shell) == InitStep(
        id="p#count", entrypoint="count:Count", package_dir=None, kind="shell", interface_hash=None, context=[],
        exit_codes={"0": "done", "1": "missing", "*": "error"},
    )
    agentic = PlanStep(
        id="shared:fix", kind="agentic", entrypoint="fix:Fix", venv="v", package_dir=None,
        lock=StepLock(name="fix", kind="agentic", entrypoint="fix:Fix", context=["previous.outputs", "process.goal"]),
    )
    assert init_step(agentic).context == ["previous.outputs", "process.goal"]
    assert (init_step(agentic).kind, init_step(agentic).exit_codes) == ("agentic", None)


def test_the_pool_runs_an_image_plan_read_back_from_process_lock_yaml(tmp_path, workspace, steps_namespace):
    site = tmp_path / "site"                          # what the step wheels install: site-packages/wynd_steps/<name>/
    extract = write_package(site / "wynd_steps" / step_module_name("invoices#extract"), EXTRACT)
    count = write_package(site / "wynd_steps" / step_module_name("invoices#count"), COUNT)
    venv_root = tmp_path / "opt" / "venvs"
    python = venv_root / "deps_1" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\ntouch "{tmp_path}/interpreter-used"\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)

    steps = {
        "invoices#extract": PlanStep(
            id="invoices#extract", kind="deterministic", entrypoint="extract:Extract", venv="deps_1", package_dir=None,
            lock=StepLock(name="extract", kind="deterministic", entrypoint="extract:Extract",
                          interface=true_interface("invoices#extract", "extract:Extract", extract)),
        ),
        "invoices#count": PlanStep(
            id="invoices#count", kind="shell", entrypoint="count:Count", venv="deps_1", package_dir=None,
            lock=StepLock(name="count", kind="shell", entrypoint="count:Count", shell=ShellLock(exit_codes=SHELL_CODES),
                          interface=true_interface("invoices#count", "count:Count", count)),
        ),
    }
    plan = RunPlan(
        mode="image", root="invoices", commit="c" * 40, provider="fake", venv_root=str(venv_root),
        venvs=[PlanVenv(id="deps_1", steps=list(steps))], steps=steps, processes={},
    )
    lock = ProcessLock(
        process="invoices", commit="c" * 40, source_sha="c" * 40, process_hash="sha256:" + "0" * 64,
        runtime_version="0.1.0", platform="linux/arm64",
        base=BaseChoice(requested="debian-slim-python", variant="slim", version="0.1.0", image="wynd-base:0.1.0-slim"),
        plan=plan,
    )
    path = tmp_path / "process.lock.yaml"
    path.write_text(dump_lock(lock))
    loaded = load_process_lock(path).plan
    assert loaded == plan

    with WorkerPool(loaded, env={**os.environ, "PYTHONPATH": str(site)}, cwd=str(tmp_path)) as pool:
        pool.start()
        assert (tmp_path / "interpreter-used").exists()          # python derived as <venv_root>/<id>/bin/python
        installed = f"wynd_steps.{step_module_name('invoices#extract')}"
        assert pool.describe("invoices#extract").cls == f"{installed}.extract:Extract"

        def run(step_id: str, inputs: dict, kind: str = "deterministic"):
            return pool.dispatch(step_id, params(step_id, inputs, workspace, kind), on_event=ignore, timeout=60)

        assert run("invoices#extract", {"lines": [1.25, 2.5]}).outputs == {"total": 3.75}
        assert run("invoices#extract", {"lines": []}).exit == "empty"
        found = run("invoices#count", {"pattern": "apple"}, "shell")
        assert (found.exit, found.outputs) == ("done", {"stdout": "2\n"})
        missing = run("invoices#count", {"pattern": "cherry"}, "shell")
        assert (missing.exit, missing.outputs) == ("missing", {"returncode": 1})


def test_a_stale_lock_snapshot_resolves_every_run_with_cause_import(tmp_path, workspace, steps_namespace):
    extract = write_package(tmp_path / "steps" / "extract", EXTRACT)
    count = write_package(tmp_path / "steps" / "count", COUNT)
    counted = true_interface("p#count", "count:Count", count)
    steps = {
        "p#extract": PlanStep(
            id="p#extract", kind="deterministic", entrypoint="extract:Extract", venv="v", package_dir=str(extract),
            lock=StepLock(name="extract", kind="deterministic", entrypoint="extract:Extract", interface=STALE),
        ),
        "p#count": PlanStep(
            id="p#count", kind="shell", entrypoint="count:Count", venv="v", package_dir=str(count),
            lock=StepLock(name="count", kind="shell", entrypoint="count:Count", interface=counted,
                          shell=ShellLock(exit_codes={0: "done", "*": "error"})),
        ),
        "p#fresh": PlanStep(
            id="p#fresh", kind="shell", entrypoint="count:Count", venv="v", package_dir=str(count),
            lock=StepLock(name="count", kind="shell", entrypoint="count:Count", interface=counted,
                          shell=ShellLock(exit_codes=SHELL_CODES)),
        ),
    }
    plan = RunPlan(mode="local", root="p", provider="fake", venv_root=str(tmp_path / "venvs"),
                   venvs=[PlanVenv(id="v", python=sys.executable, steps=list(steps))], steps=steps, processes={})
    with WorkerPool(plan) as pool:
        for step_id, inputs, kind in (("p#extract", {"lines": [1.0]}, "deterministic"),
                                      ("p#count", {"pattern": "apple"}, "shell")):
            result = pool.dispatch(step_id, params(step_id, inputs, workspace, kind), on_event=ignore, timeout=60)
            outputs = result.outputs
            assert (result.exit, outputs["cause"], outputs["type"]) == ("error", "import", "SnapshotDrift")
            assert result.outputs["message"] == DRIFT_MESSAGE.format(step_id)
            with pytest.raises(StepDefinitionError, match=f"step {step_id} failed to import: SnapshotDrift"):
                pool.describe(step_id)
        fresh = pool.dispatch("p#fresh", params("p#fresh", {"pattern": "apple"}, workspace, "shell"),
                              on_event=ignore, timeout=60)
        assert fresh.exit == "done"


def test_each_step_runs_in_the_worker_of_its_own_venv(tmp_path, workspace):
    extract = write_package(tmp_path / "steps" / "extract", EXTRACT)
    count = write_package(tmp_path / "steps" / "count", COUNT)
    steps = {
        "p#extract": PlanStep(id="p#extract", kind="deterministic", entrypoint="extract:Extract", venv="plain",
                              package_dir=str(extract),
                              lock=StepLock(name="extract", kind="deterministic", entrypoint="extract:Extract")),
        "p#count": PlanStep(id="p#count", kind="shell", entrypoint="count:Count", venv="tools", package_dir=str(count),
                            lock=StepLock(name="count", kind="shell", entrypoint="count:Count")),
    }
    plan = RunPlan(
        mode="local", root="p", provider="fake", venv_root=str(tmp_path / "venvs"),
        venvs=[PlanVenv(id="plain", python=sys.executable, steps=["p#extract"]),
               PlanVenv(id="tools", python=sys.executable, steps=["p#count"])],
        steps=steps, processes={},
    )
    with WorkerPool(plan) as pool:
        result = pool.dispatch("p#extract", params("p#extract", {"lines": [2.0]}, workspace), on_event=ignore,
                               timeout=60)
        assert result.outputs == {"total": 2.0}
        plain, tools = pool.status()
        assert (plain["alive"], tools["alive"]) == (True, False)              # workers start lazily, per venv
        served = pool.call("plain", "describe", {"ids": None}, on_event=ignore, timeout=30)["steps"]
        assert list(served) == ["p#extract"]
        result = pool.dispatch("p#count", params("p#count", {"pattern": "banana"}, workspace, "shell"),
                               on_event=ignore, timeout=60)
        assert result.outputs == {"stdout": "1\n"}
        plain, tools = pool.status()
        assert tools["alive"] and tools["pid"] != plain["pid"]
        assert list(pool.call("tools", "describe", {"ids": None}, on_event=ignore, timeout=30)["steps"]) == ["p#count"]

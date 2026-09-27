"""Bake (`$DRAFTS/04 §8.9`, PLAN §15 item 47): a stdlib zipapp with an extract-once bootstrap, built from the build
context the prepare phase writes. Offline with real `uv` (installs from the warm cache).

PROC-BUILD's `prepare_build_job` and `UvResolver` land in the same sub-wave, so these tests write the build context
(`process.lock.yaml`, `process.env.yaml`, `dist/<step wheel>`) themselves and resolve with a real-`uv` double.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

import wynd.process.build.job as build_job
from wynd.process import _proc
from wynd.process.artefacts import BuildInfo, LocalArtefactStore
from wynd.process.bake import (
    BAKE_VENV,
    NotBakeable,
    bake_process,
    is_runtime,
    run_bake_job,
    runtime_requirements,
)
from wynd.process.errors import ResolutionError
from wynd.process.jobs import JobContext, JobRecord
from wynd.process.venvs import RuntimeSource
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.lockfiles import BaseChoice, LockedVenv, LockedWheel, ProcessLock, StepLock, dump_lock
from wynd.spec.plan import PlanNode, PlanProcess, PlanStep, PlanVenv, RunPlan
from wynd.spec.process_doc import ProcessDoc
from wynd.spec.workspace import step_module_name
from wynd.spec.yamlio import parse_model

SID = "doubler#double"
MODULE = step_module_name(SID)
COMMIT = "c" * 40
HASH = "sha256:" + "0" * 64
PINS = ["pyyaml==6.0.3"]

DOUBLE_PY = '''
    """Double a number."""
    from typing import Literal

    import yaml
    from pydantic import BaseModel

    from wynd.runtime import DeterministicStep


    class Double(DeterministicStep):
        """Double x."""

        class Input(BaseModel):
            x: int

        class Done(BaseModel):
            exit: Literal["done"] = "done"
            y: int
            text: str

        Output = Done

        def run(self, input):
            return self.Done(y=2 * input.x, text=yaml.safe_dump({"y": 2 * input.x}).strip())
'''

PROCESS = """
    kind: process
    name: doubler
    entry: double
    inputs:
      x: integer
    outputs:
      y: integer
      text: string
    steps:
      double: {use: ./steps/double}
    edges:
      - from: double.done
        to: $exit.done
        with: {y: steps.double.outputs.y, text: steps.double.outputs.text}
"""

STEP_PYPROJECT = f"""
[project]
name = "wynd-step-{MODULE.replace('_', '-')}"
version = "0.1.0"
requires-python = ">=3.12"
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"
[tool.hatch.build.targets.wheel]
packages = ["wynd_steps"]
"""


class UvCompile:
    """Resolver double: `uv pip compile` for the host (what `UvResolver.compile(universal=False)` does)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def compile(self, requirements, *, universal, python_version="3.12", python_platform=None, only_binary=False,
                find_links=()):
        assert universal is False
        self.calls.append(list(requirements))
        proc = subprocess.run(
            [_proc.require_tool("uv"), "pip", "compile", "--no-header", "--no-annotate", "--python-version",
             python_version, "-"],
            input="\n".join(requirements), capture_output=True, text=True, cwd="/",
        )
        if proc.returncode != 0:
            raise ResolutionError(proc.stderr.strip())
        return [line for line in proc.stdout.splitlines() if line and not line.startswith("#")]

    def build_wheel(self, src, out_dir):
        raise AssertionError("bake never builds step wheels")


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("UV_OFFLINE", "1")


@pytest.fixture(scope="module")
def step_wheel(tmp_path_factory) -> Path:
    """The fixture step as the builder stages it: `wynd_steps/<module name>/`, built with `uv build`."""
    stage = tmp_path_factory.mktemp("stage")
    pkg = stage / "wynd_steps" / MODULE
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "double.py").write_text(textwrap.dedent(DOUBLE_PY).lstrip())
    (stage / "pyproject.toml").write_text(STEP_PYPROJECT)
    out = tmp_path_factory.mktemp("dist")
    subprocess.run([_proc.require_tool("uv"), "build", "--wheel", "--out-dir", str(out), str(stage)], check=True,
                   capture_output=True, cwd=stage.parent, env={**os.environ, "UV_OFFLINE": "1"})
    [wheel] = out.glob("*.whl")
    return wheel


def make_lock(wheel: Path, *, venvs: list[LockedVenv] | None = None, system: list[str] | None = None,
              kind: str = "deterministic") -> ProcessLock:
    doc = parse_model(textwrap.dedent(PROCESS), ProcessDoc)
    step_lock = StepLock(name="double", kind=kind, entrypoint="double:Double", fragment={"deps": ["pyyaml>=6"]})
    plan = RunPlan(
        mode="image", root="doubler", commit=COMMIT, provider="fake", venv_root="/opt/wynd/venvs",
        venvs=[PlanVenv(id="g1", steps=[SID])],
        steps={SID: PlanStep(id=SID, kind=kind, entrypoint="double:Double", venv="g1", package_dir=None,
                             lock=step_lock)},
        processes={"doubler": PlanProcess(id="doubler", dir=None, definition=doc,
                                          nodes={"double": PlanNode(step=SID)})},
        edge_venvs={"doubler:double.done[0]": "g1"},
    )
    return ProcessLock(
        process="doubler", commit=COMMIT, source_sha=COMMIT, process_hash=HASH, runtime_version="0.1.0",
        platform="linux/arm64",
        base=BaseChoice(requested="debian-slim-python", variant="slim", version="0.1.0",
                        image="wynd-base:0.1.0-slim"),
        system_packages=system or [],
        venvs=venvs or [LockedVenv(id="g1", inputs=["pyyaml>=6"], requirements=[*PINS, "wynd-runtime==0.1.0"])],
        wheels={SID: LockedWheel(dir="processes/doubler/steps/double", hash=HASH, wheel=f"dist/{wheel.name}")},
        plan=plan,
    )


def write_context(context: Path, lock: ProcessLock, wheel: Path) -> None:
    """What `prepare_build_job` leaves in the build context dir (the parts a bake reads, plus a Dockerfile)."""
    (context / "dist").mkdir(parents=True)
    (context / "dist" / wheel.name).write_bytes(wheel.read_bytes())
    (context / "process.lock.yaml").write_text(dump_lock(lock))
    (context / "process.env.yaml").write_text(dump_lock(EnvManifest(process="doubler", commit=COMMIT, vars=[])))
    (context / "Dockerfile").write_text("FROM wynd-base:0.1.0-slim\n")


def make_ctx(tmp_path: Path, logs: list[str]) -> JobContext:
    now = datetime.now(UTC)
    record = JobRecord(id="job_1", job_kind="bake", process="doubler", ref=COMMIT, base_commit=COMMIT,
                       inputs={"process": "doubler"}, status="running", runner="inprocess",
                       handler="wynd.process.bake:run_bake_job", created_at=now, updated_at=now)
    ws = tmp_path / "ws"
    state = ws / ".wynd"
    scratch = state / "jobs" / record.id / "scratch"
    scratch.mkdir(parents=True)
    return JobContext(job=record, inputs=dict(record.inputs), session=None, worktree=ws, workspace=ws,
                      workspace_root=ws, state_dir=state, scratch=scratch, runs=None, registry=None,
                      log=logs.append, commit=lambda message, paths=None: None, save_session=lambda session: None)


@pytest.fixture
def ctx(tmp_path):
    return make_ctx(tmp_path, [])


# The bare uv-managed CPython behind the test venv: no wynd, pydantic or pyyaml, so the archive must be self-contained.
BARE_PYTHON = Path(sys.base_prefix) / "bin" / "python3.12"


def run_pyz(pyz: Path, home: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "VIRTUAL_ENV")}
    env["WYND_HOME"] = str(home)
    return subprocess.run([str(BARE_PYTHON), str(pyz), *args], capture_output=True, text=True, env=env, input=stdin,
                          cwd=home, timeout=120)


def test_a_pure_python_process_bakes_into_a_pyz_that_runs(ctx, step_wheel, tmp_path):
    lock = make_lock(step_wheel)
    write_context(ctx.scratch / "ctx", lock, step_wheel)
    resolver = UvCompile()
    pyz = bake_process(ctx, None, resolver=resolver)

    assert pyz == ctx.scratch / "bake" / "doubler.pyz"
    assert pyz.read_bytes().startswith(b"#!/usr/bin/env python3.12\n")
    [requested] = resolver.calls
    assert requested[0] == "pyyaml==6.0.3"                        # the lock's pins, minus the runtime's own pin
    assert not any(req.startswith("wynd-runtime==") for req in requested)
    assert any(req.startswith("wynd-runtime @ file://") for req in requested)   # editable monorepo -> directory

    with zipfile.ZipFile(pyz) as zf:
        names = set(zf.namelist())
        meta = json.loads(zf.read("_wynd_bake/meta.json"))
        plan = RunPlan.model_validate_json(zf.read("_wynd_bake/plan.json"))
    assert {"__main__.py", "_wynd_bake/process.env.yaml", f"site/wynd_steps/{MODULE}/double.py",
            "site/wynd/runtime/bake.py", "site/wynd/spec/plan.py", "site/yaml/__init__.py"} <= names
    assert not any(name.startswith("site/wynd_steps/") and "test_" in name for name in names)
    assert set(meta) == {"id", "process", "commit", "platform", "wynd_version"}
    assert (meta["process"], meta["commit"], meta["wynd_version"], len(meta["id"])) == ("doubler", COMMIT, "0.1.0", 16)
    assert [venv.id for venv in plan.venvs] == [BAKE_VENV] and plan.venvs[0].steps == [SID]
    assert plan.venvs[0].python is None and plan.venv_root == ""
    assert {step.venv for step in plan.steps.values()} == {BAKE_VENV}
    assert plan.edge_venvs == {"doubler:double.done[0]": BAKE_VENV}
    assert plan.mode == "image" and plan.steps[SID].package_dir is None

    home = tmp_path / "home"
    home.mkdir()
    first = run_pyz(pyz, home, '{"x": 1}')
    assert first.returncode == 0, first.stderr
    printed = json.loads(first.stdout)
    assert printed["exit"] == "done"
    assert printed["outputs"] == {"y": 2, "text": "y: 2"}

    root = home / "bake" / meta["id"]
    assert (root / ".complete").is_file()
    scripts = [path for path in (root / "site" / "bin").iterdir() if path.is_file()]
    assert scripts and all(os.access(path, os.X_OK) for path in scripts)   # exec bits restored from the archive


def test_a_second_run_skips_extraction(ctx, step_wheel, tmp_path):
    write_context(ctx.scratch / "ctx", make_lock(step_wheel), step_wheel)
    pyz = bake_process(ctx, None, resolver=UvCompile())
    home = tmp_path / "home"
    home.mkdir()
    assert run_pyz(pyz, home, '{"x": 2}').returncode == 0
    [root] = (home / "bake").iterdir()
    marker = root / ".complete"
    stamp = marker.stat().st_mtime_ns
    (root / "sentinel").write_text("kept")

    second = run_pyz(pyz, home, stdin='{"x": 3}')
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["outputs"]["y"] == 6
    assert (root / "sentinel").read_text() == "kept"
    assert marker.stat().st_mtime_ns == stamp
    assert [path.name for path in (home / "bake").iterdir()] == [root.name]   # no temp dirs left behind


def test_an_incomplete_extraction_is_replaced(ctx, step_wheel, tmp_path):
    write_context(ctx.scratch / "ctx", make_lock(step_wheel), step_wheel)
    pyz = bake_process(ctx, None, resolver=UvCompile())
    with zipfile.ZipFile(pyz) as zf:
        bake_id = json.loads(zf.read("_wynd_bake/meta.json"))["id"]
    home = tmp_path / "home"
    (home / "bake" / bake_id / "site").mkdir(parents=True)          # a crashed extraction: no .complete marker
    result = run_pyz(pyz, home, '{"x": 4}')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["outputs"]["y"] == 8


def test_a_process_with_system_packages_is_not_bakeable(ctx, step_wheel):
    write_context(ctx.scratch / "ctx", make_lock(step_wheel, system=["poppler-utils"]), step_wheel)
    with pytest.raises(NotBakeable) as err:
        bake_process(ctx, None, resolver=UvCompile())
    assert str(err.value) == ("not bakeable: process doubler needs system packages (poppler-utils); "
                              "use wynd build")
    assert not (ctx.scratch / "bake").exists()


def test_a_process_with_a_shell_step_is_not_bakeable(ctx, step_wheel):
    write_context(ctx.scratch / "ctx", make_lock(step_wheel, kind="shell"), step_wheel)
    with pytest.raises(NotBakeable) as err:
        bake_process(ctx, None, resolver=UvCompile())
    assert str(err.value) == "not bakeable: shell steps need an image (doubler#double); use wynd build"


def test_conflicting_dependency_sets_are_not_bakeable(ctx, step_wheel):
    venvs = [LockedVenv(id="g1", inputs=[], requirements=["pyyaml==6.0.3"]),
             LockedVenv(id="g2", inputs=[], requirements=["pyyaml==6.0.2"])]
    write_context(ctx.scratch / "ctx", make_lock(step_wheel, venvs=venvs), step_wheel)
    with pytest.raises(NotBakeable) as err:
        bake_process(ctx, None, resolver=UvCompile())
    message = str(err.value)
    assert message.startswith("not bakeable: steps' dependency sets conflict (")
    assert "pyyaml" in message and message.endswith("); use wynd build")


def test_the_build_context_dir_env_var_is_honoured(ctx, step_wheel, tmp_path, monkeypatch):
    context = tmp_path / "shared-ctx"
    write_context(context, make_lock(step_wheel, system=["curl"]), step_wheel)
    monkeypatch.setenv("WYND_BUILD_CONTEXT_DIR", str(context))
    with pytest.raises(NotBakeable, match=r"\(curl\)"):
        bake_process(ctx, None, resolver=UvCompile())


def test_runtime_requirements_and_pins():
    pinned = RuntimeSource("0.1.0", None)
    assert runtime_requirements(pinned) == ["wynd-spec==0.1.0", "wynd-runtime==0.1.0"]
    editable = RuntimeSource("0.1.0", (Path("/src/packages/spec"), Path("/src/packages/runtime")))
    assert runtime_requirements(editable) == ["wynd-spec @ file:///src/packages/spec",
                                              "wynd-runtime @ file:///src/packages/runtime"]
    for pin in ["wynd-spec==0.1.0", "Wynd_Runtime==0.1.0", "wynd-runtime @ file:///x", "-e file:///src/spec"]:
        assert is_runtime(pin), pin
    for pin in ["pyyaml==6.0.3", "wynd-step-read==0.1.0", "wyndx==1"]:
        assert not is_runtime(pin), pin


# --- the job handler ----------------------------------------------------------------------------------------------


@pytest.fixture
def prepare(monkeypatch, step_wheel):
    """`prepare_build_job` double: records the ctx it got and writes the build context."""
    calls = []

    def fake_prepare(job_ctx):
        calls.append(job_ctx)
        write_context(job_ctx.scratch / "ctx", make_lock(step_wheel, system=fake_prepare.system), step_wheel)
        return object()                                          # bake reads the context dir, not Prepared fields

    fake_prepare.system = None
    monkeypatch.setattr(build_job, "prepare_build_job", fake_prepare)
    fake_prepare.calls = calls
    return fake_prepare


def test_the_bake_job_stores_the_pyz_with_the_build_of_its_commit(tmp_path, prepare, monkeypatch):
    monkeypatch.setattr("wynd.process.build.resolve.UvResolver", UvCompile)
    logs: list[str] = []
    job_ctx = make_ctx(tmp_path, logs)
    outcome = run_bake_job(job_ctx)

    assert outcome.status == "succeeded", outcome.error
    [prepared_ctx] = prepare.calls
    assert prepared_ctx.inputs == {"process": "doubler", "registry": None, "push": False}
    build_dir = job_ctx.state_dir / "build" / "doubler" / COMMIT
    assert outcome.artefacts == {"path": str(build_dir / "doubler.pyz"),
                                 "size_bytes": (build_dir / "doubler.pyz").stat().st_size, "commit": COMMIT}
    info = LocalArtefactStore(job_ctx.state_dir).get_build("doubler", COMMIT)
    assert info.bake == "doubler.pyz" and info.image is None and info.job_id == "job_1"
    assert info.process_hash == HASH and info.base["image"] == "wynd-base:0.1.0-slim"
    assert {"process.lock.yaml", "process.env.yaml", "dist", "doubler.pyz", "build.json"} <= {
        path.name for path in build_dir.iterdir()}
    assert any("baked doubler" in line for line in logs)


def test_the_bake_job_keeps_an_existing_image_build(tmp_path, prepare, monkeypatch):
    monkeypatch.setattr("wynd.process.build.resolve.UvResolver", UvCompile)
    job_ctx = make_ctx(tmp_path, [])
    store = LocalArtefactStore(job_ctx.state_dir)
    staged = tmp_path / "image-build"
    staged.mkdir()
    (staged / "buildkit-metadata.json").write_text("{}")
    image = BuildInfo(process="doubler", commit=COMMIT, source_sha=COMMIT, job_id="job_0", process_hash=HASH,
                      image="wynd/doubler:cccccccccccc", image_id="sha256:1", image_digest="sha256:2",
                      base={"image": "wynd-base:0.1.0-slim"}, manifest=EnvManifest(process="doubler", vars=[]),
                      created_at=datetime(2026, 1, 1, tzinfo=UTC), dir="")
    store.put_build(staged, image)

    outcome = run_bake_job(job_ctx)
    assert outcome.status == "succeeded", outcome.error
    info = store.get_build("doubler", COMMIT)
    assert (info.image, info.image_digest, info.job_id, info.bake) == (
        "wynd/doubler:cccccccccccc", "sha256:2", "job_0", "doubler.pyz")
    build_dir = Path(info.dir)
    assert (build_dir / "buildkit-metadata.json").is_file() and (build_dir / "doubler.pyz").is_file()


def test_an_unbakeable_process_fails_the_job_with_the_message(tmp_path, prepare, monkeypatch):
    monkeypatch.setattr("wynd.process.build.resolve.UvResolver", UvCompile)
    prepare.system = ["libmagic1"]
    job_ctx = make_ctx(tmp_path, [])
    outcome = run_bake_job(job_ctx)
    assert outcome.status == "failed"
    assert outcome.error == "not bakeable: process doubler needs system packages (libmagic1); use wynd build"
    assert LocalArtefactStore(job_ctx.state_dir).get_build("doubler", COMMIT) is None


def test_a_failing_prepare_phase_fails_the_job_with_its_report(tmp_path, monkeypatch):
    from wynd.process.errors import WyndProcessError

    class TestsFail(WyndProcessError):
        def __init__(self) -> None:
            self.report = {"passed": 0, "failed": 1}
            super().__init__(f"tests fail on {COMMIT}")

    def failing_prepare(job_ctx):
        raise TestsFail()

    monkeypatch.setattr(build_job, "prepare_build_job", failing_prepare)
    job_ctx = make_ctx(tmp_path, [])
    outcome = run_bake_job(job_ctx)
    assert (outcome.status, outcome.error, outcome.report) == ("failed", f"tests fail on {COMMIT}",
                                                               {"passed": 0, "failed": 1})
    assert not (job_ctx.scratch / "bake").exists()

"""The build job with a fake resolver and image builder (PLAN §6.5, §3.18; `$DRAFTS/04 §18.5`).

Goldens live in `golden/`; `WYND_UPDATE_GOLDEN=1` rewrites them (review the diff).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from support.proc_build_fakes import (
    BUILD_DIGEST,
    CONFIG_DIGEST,
    GATED,
    IMAGE_BYTES,
    PUSH_DIGEST,
    FakeImageBuilder,
    FakeResolver,
    build_ctx,
    gated_files,
)
from support.proc_env_workspaces import MemRegistry, make_family, validate_double  # noqa: F401

import wynd.process.build.job as job
import wynd.process.testing as testing
import wynd.process.validation as validation
from wynd.process.build.dockerfile import manifest_label, render_dockerfile
from wynd.process.git import dirty_paths
from wynd.process.hashing import process_hash
from wynd.process.testing import SuiteResult, TestReport
from wynd.process.venvs import RuntimeSource
from wynd.process.workspace import load_workspace
from wynd.runtime.storage import stores_from_env
from wynd.runtime.storage.models import TestResult
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.errors import Diagnostic
from wynd.spec.lockfiles import load_process_lock
from wynd.spec.workspace import step_module_name
from wynd.spec.yamlio import load_model

GOLDEN = Path(__file__).parent / "golden"
PINNED = RuntimeSource("0.1.0", None)
ARM = "linux/arm64"


def golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if os.environ.get("WYND_UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    assert text == path.read_text(), f"golden {name} differs (WYND_UPDATE_GOLDEN=1 rewrites it)"


@pytest.fixture
def runs(tmp_path):
    return stores_from_env({"WYND_HOME": str(tmp_path / "home")}, data_dir=tmp_path / "data").runs


@pytest.fixture
def fakes(monkeypatch):
    resolver, builder = FakeResolver(), FakeImageBuilder()
    monkeypatch.setattr(job, "UvResolver", lambda: resolver)
    monkeypatch.setattr(job, "open_image_builder", lambda environ=None: builder)
    monkeypatch.setattr(job, "detect_runtime_source", lambda environ: PINNED)
    monkeypatch.delenv("WYND_BASE_REPO", raising=False)
    monkeypatch.delenv("WYND_BUILD_CONTEXT_DIR", raising=False)
    return resolver, builder


@pytest.fixture
def no_test_run(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the replay tests must not run when a passing result is recorded")

    monkeypatch.setattr(testing, "run_tests", refuse)


def record_passing(ws: Path, runs, pid: str) -> str:
    """Record a passing test result for the process at its closure HEAD (HEAD of the fresh repo)."""
    from wynd.process.git import closure_head, reference_closure

    workspace = load_workspace(ws)
    commit = closure_head(ws, reference_closure(workspace, pid))
    key = f"process:{pid}:{process_hash(workspace.tree, workspace.load_process(pid))}"
    runs.put_test_result(TestResult(commit=commit, key=key, passed=True,
                                    counts={"passed": 5, "failed": 0, "error": 0, "skipped": 1},
                                    ran_at=datetime.now(UTC)))
    return commit


@pytest.fixture
def gated(make_repo, runs, validate_double):
    ws = make_repo(files=gated_files())
    commit = record_passing(ws, runs, "gated")
    return ws, commit


def placeholders(text: str, commit: str) -> str:
    return text.replace(commit, "<commit>")


# --- goldens --------------------------------------------------------------------------------------------------------

def test_gated_lock_and_dockerfile_goldens(gated, runs, fakes, no_test_run):
    """A deterministic-only process with one agentic branch: the edge venv (claude-code deps, no steps) is in the
    lock's venvs, its plan and the Dockerfile."""
    ws, commit = gated
    ctx = build_ctx(ws, runs, MemRegistry(), {"process": "gated", "registry": None, "push": False, "platform": ARM})
    prepared = job.prepare_build_job(ctx)
    context = Path(prepared.context_dir)
    lock_text = (context / "process.lock.yaml").read_text()
    golden("gated/process.lock.yaml", placeholders(lock_text, commit))
    lock = load_process_lock(context / "process.lock.yaml")
    manifest = load_model(context / "process.env.yaml", EnvManifest)
    assert (context / "Dockerfile").read_text() == render_dockerfile(lock) + manifest_label(manifest)
    golden("gated/Dockerfile", placeholders(render_dockerfile(lock), commit))

    sdk_venv = lock.plan.edge_venvs["gated:read.done[keep]"]
    assert [v.steps for v in lock.plan.venvs if v.id == sdk_venv] == [[]]
    assert [v.inputs for v in lock.venvs if v.id == sdk_venv] == [["claude-agent-sdk>=0.2.157,<0.3"]]
    assert sorted(p.name for p in (context / "venvs").iterdir()) == sorted(v.id for v in lock.venvs)
    assert lock.plan.mode == "image" and lock.plan.venv_root == "/opt/wynd/venvs" and lock.plan.commit == commit
    assert (context / "image.ref").read_text() == f"wynd/gated:{commit[:12]}\n"


def test_the_lock_is_byte_identical_for_the_same_commit(gated, runs, fakes, no_test_run, tmp_path):
    ws, commit = gated
    texts = []
    for n in (1, 2):
        ctx = build_ctx(ws, runs, MemRegistry(), {"process": "gated", "platform": ARM}, job_id=f"job_{n}")
        prepared = job.prepare_build_job(ctx)
        texts.append((Path(prepared.context_dir) / "process.lock.yaml").read_text())
    assert texts[0] == texts[1]
    lock = yaml.safe_load(texts[0])
    assert (lock["commit"], lock["source_sha"]) == (commit, commit)
    assert lock["base"] == {"requested": "debian-slim-python", "variant": "slim", "version": "0.1.0",
                            "image": "wynd-base:0.1.0-slim"}
    assert "created_at" not in texts[0] and "ran_at" not in texts[0]


# --- the gate and the failures before anything is built ------------------------------------------------------------

def test_a_design_phase_process_fails(make_repo, runs, fakes, validate_double):
    gated = {**GATED, "steps": {**GATED["steps"], "note": {"use": "./steps/note"}}}
    files = {**gated_files(gated), "processes/gated/proto/note.yaml": (
        "kind: proto_step\nname: note\ninstruction: Write a note.\ninputs:\n  text: string\noutputs:\n  text: string\n")}
    ws = make_repo(files=files)
    outcome = job.run_build_job(build_ctx(ws, runs, MemRegistry(), {"process": "gated", "platform": ARM}))
    assert outcome.status == "failed"
    assert outcome.error == "process is in design phase: steps gated#note are not compiled (run wynd compile gated)"
    assert fakes[0].calls == [] and fakes[1].calls == []


def test_validation_errors_fail_with_the_report(make_repo, runs, fakes, monkeypatch):
    ws = make_repo(files=gated_files())

    def failing(lp, *, providers=None, stats=None):
        return validation.ValidationReport(process=lp.id, normalized={}, env_refs={},
                                           diagnostics=[Diagnostic("error", "E209", "step 'save' is unreachable")])

    monkeypatch.setattr(validation, "validate", failing)
    outcome = job.run_build_job(build_ctx(ws, runs, MemRegistry(), {"process": "gated", "platform": ARM}))
    assert outcome.status == "failed"
    assert outcome.error.startswith("process 'gated' failed validation: 1 error(s)")
    assert [d["code"] for d in outcome.report["diagnostics"]] == ["E209"]
    assert fakes[1].calls == []


def test_a_recorded_passing_result_skips_the_tests(gated, runs, fakes, no_test_run):
    ws, commit = gated
    outcome = job.run_build_job(build_ctx(ws, runs, MemRegistry(), {"process": "gated", "platform": ARM}))
    assert outcome.status == "succeeded", outcome.error
    assert outcome.artefacts["tests"] == {"passed": 5, "failed": 0, "total": 6, "source": "registry"}


def fake_report(passed: bool):
    def run_tests(ws, pid, *, mode, commit, runs, venv_root, scratch, env=None, log=None):
        run_tests.calls.append({"pid": pid, "mode": mode, "commit": commit, "runs": runs, "venv_root": venv_root})
        now = datetime.now(UTC)
        suite = SuiteResult(subject="gated#read", hash="h", passed=passed,
                            counts={"passed": 3, "failed": 0 if passed else 1, "error": 0, "skipped": 0}, cases=[])
        return TestReport(process=pid, commit=commit, process_hash="p", mode=mode, passed=passed, suites=[suite],
                          started_at=now, finished_at=now, recorded=True)

    run_tests.calls = []
    return run_tests


def test_without_a_record_the_replay_tests_run_at_the_closure_head(make_repo, runs, fakes, validate_double,
                                                                   monkeypatch):
    ws = make_repo(files=gated_files())
    fake = fake_report(passed=True)
    monkeypatch.setattr(testing, "run_tests", fake)
    ctx = build_ctx(ws, runs, MemRegistry(), {"process": "gated", "platform": ARM})
    outcome = job.run_build_job(ctx)
    assert outcome.status == "succeeded", outcome.error
    head = ctx.job.ref
    assert fake.calls == [{"pid": "gated", "mode": "replay", "commit": head, "runs": runs,
                           "venv_root": ws / ".wynd" / "venvs"}]
    assert outcome.artefacts["tests"] == {"passed": 3, "failed": 0, "total": 3, "source": "ran"}


def test_failing_tests_fail_the_build(make_repo, runs, fakes, validate_double, monkeypatch):
    ws = make_repo(files=gated_files())
    monkeypatch.setattr(testing, "run_tests", fake_report(passed=False))
    ctx = build_ctx(ws, runs, MemRegistry(), {"process": "gated", "platform": ARM})
    outcome = job.run_build_job(ctx)
    assert (outcome.status, outcome.error) == ("failed", f"tests fail on {ctx.job.ref}")
    assert outcome.report["passed"] is False
    assert fakes[1].calls == []


def test_a_childs_failing_example_fails_the_parents_build(make_repo, commit, runs, fakes, validate_double):
    """Real replay tests: the child's own example is part of the parent's gate (PLAN §6.5, §15 item 67)."""
    ws = make_family(make_repo, commit, child_expected=5)
    ctx = build_ctx(ws, runs, MemRegistry(), {"process": "parent", "platform": ARM})
    outcome = job.run_build_job(ctx)
    assert (outcome.status, outcome.error) == ("failed", f"tests fail on {ctx.job.ref}")
    suites = {suite["subject"]: suite["passed"] for suite in outcome.report["suites"]}
    assert suites == {"child#double": True, "process:parent": True, "process:child": False}
    assert fakes[1].calls == []


def test_an_unknown_registry_fails_before_anything_is_built(gated, runs, fakes):
    ws, _ = gated
    ctx = build_ctx(ws, runs, MemRegistry(), {"process": "gated", "registry": "ghcr", "push": True, "platform": ARM})
    outcome = job.run_build_job(ctx)
    assert (outcome.status, outcome.error) == ("failed", "unknown image registry 'ghcr'")
    assert fakes[0].calls == [] and fakes[1].calls == []
    assert not (ctx.scratch / "ctx").exists()


# --- a whole build --------------------------------------------------------------------------------------------------

def test_the_build_dir_layout_and_outcome(gated, runs, fakes, no_test_run):
    ws, commit = gated
    resolver, builder = fakes
    ctx = build_ctx(ws, runs, MemRegistry(), {"process": "gated", "registry": None, "push": False, "platform": ARM})
    outcome = job.run_build_job(ctx)
    assert outcome.status == "succeeded", outcome.error
    build_dir = ws / ".wynd" / "build" / "gated" / commit
    files = sorted(p.relative_to(build_dir).as_posix() for p in build_dir.rglob("*") if p.is_file())
    lock = load_process_lock(build_dir / "process.lock.yaml")
    assert files == sorted([
        "Dockerfile", "process.lock.yaml", "process.env.yaml", "build.json",
        *(f"venvs/{v.id}/requirements.txt" for v in lock.venvs),
        *(w.wheel for w in lock.wheels.values()),
    ])
    assert {sid: w.wheel for sid, w in lock.wheels.items()} == {
        sid: f"dist/wynd_step_{step_module_name(sid)}-0.1.0-py3-none-any.whl"
        for sid in ("gated#read", "gated#save", "helper#count")}
    image = f"wynd/gated:{commit[:12]}"
    assert builder.calls == [("build", (image,))]
    request = builder.requests[0]
    assert (request.context, request.dockerfile, request.platforms) == (
        ctx.scratch / "ctx", ctx.scratch / "ctx" / "Dockerfile", (ARM,))
    wheels_bytes = sum(p.stat().st_size for p in (build_dir / "dist").glob("*.whl"))
    assert outcome.artefacts == {
        "commit": commit, "image": image, "image_digest": BUILD_DIGEST, "build_dir": str(build_dir),
        "pushed": False, "tests": {"passed": 5, "failed": 0, "total": 6, "source": "registry"},
        "sizes": {"build_dir_bytes": sum(p.stat().st_size for p in build_dir.rglob("*") if p.is_file()),
                  "image_bytes": IMAGE_BYTES, "wheels_bytes": wheels_bytes},
    }
    info = yaml.safe_load((build_dir / "build.json").read_text())
    assert (info["image"], info["image_id"], info["job_id"], info["pushed"]) == (image, CONFIG_DIGEST, ctx.job.id, [])
    assert info["manifest"]["process"] == "gated" and info["manifest"]["commit"] == commit
    requirements = (build_dir / "venvs" / lock.plan.steps["gated#read"].venv / "requirements.txt").read_text()
    assert requirements == "pypdf==1.0.0\nwynd-runtime==0.1.0\nwynd-spec==0.1.0\n"
    assert all(call["universal"] for call in resolver.calls if call["op"] == "compile")
    assert dirty_paths(ws, None) == []                     # nothing under the tracked tree changed


def test_wheels_carry_no_tier_and_skip_tests(gated, runs, fakes, no_test_run):
    import json
    import zipfile

    ws, commit = gated
    job.run_build_job(build_ctx(ws, runs, MemRegistry(), {"process": "gated", "platform": ARM}))
    wheel = next((ws / ".wynd" / "build" / "gated" / commit / "dist").glob("wynd_step_read_*.whl"))
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        meta = json.loads(zf.read(next(n for n in names if n.endswith("wynd-step.json"))))
    module = wheel.name.removeprefix("wynd_step_").removesuffix("-0.1.0-py3-none-any.whl")
    assert sorted(names) == [f"wynd_steps/{module}/read.py", f"wynd_steps/{module}/wynd-step.json"]
    assert meta["step"] == "gated#read" and meta["requirements"] == ["pypdf>=6,<7"]
    assert not {"tier", "thinking", "provider"} & set(meta)


def test_push_logs_in_tags_and_pushes_in_that_order(gated, runs, fakes, no_test_run, monkeypatch):
    ws, commit = gated
    _, builder = fakes
    monkeypatch.setenv("GHCR_USER", "peter")
    monkeypatch.setenv("GHCR_TOKEN", "s3cret")
    registry = MemRegistry({"registries": {"ghcr": {"url": "ghcr.io/peterddod", "username_env": "GHCR_USER",
                                                    "password_env": "GHCR_TOKEN"}}})
    ctx = build_ctx(ws, runs, registry, {"process": "gated", "registry": "ghcr", "push": True, "platform": ARM})
    outcome = job.run_build_job(ctx)
    assert outcome.status == "succeeded", outcome.error
    local, remote = f"wynd/gated:{commit[:12]}", f"ghcr.io/peterddod/gated:{commit[:12]}"
    assert builder.calls == [("build", (local,)), ("login", "ghcr.io", "peter", "s3cret"), ("tag", local, remote),
                             ("push", remote)]
    assert (outcome.artefacts["image"], outcome.artefacts["image_digest"], outcome.artefacts["pushed"]) == (
        remote, PUSH_DIGEST, True)
    assert outcome.report["pushed"] == [f"{remote}@{PUSH_DIGEST}"]
    assert "s3cret" not in (ws / ".wynd" / "build" / "gated" / commit / "build.json").read_text()


def test_a_registry_without_push_builds_locally_only(gated, runs, fakes, no_test_run):
    ws, commit = gated
    registry = MemRegistry({"registries": {"ghcr": {"url": "ghcr.io/peterddod"}}})
    ctx = build_ctx(ws, runs, registry, {"process": "gated", "registry": "ghcr", "push": False, "platform": ARM})
    outcome = job.run_build_job(ctx)
    assert outcome.artefacts["image"] == f"wynd/gated:{commit[:12]}" and outcome.artefacts["pushed"] is False
    assert [call[0] for call in fakes[1].calls] == ["build"]


def test_a_missing_local_base_is_built_first(gated, runs, fakes, no_test_run, monkeypatch):
    ws, _ = gated
    _, builder = fakes
    builder.present.clear()
    built = []
    import wynd.process.base as base

    monkeypatch.setattr(base, "build_base", lambda version, variants, **kw: built.append((version, variants,
                                                                                          kw["repo"])))
    monkeypatch.setattr("wynd.process.venvs.detect_runtime_source",
                        lambda environ: RuntimeSource("0.1.0", (Path("/src/spec"), Path("/src/runtime"))))
    outcome = job.run_build_job(build_ctx(ws, runs, MemRegistry(), {"process": "gated", "platform": ARM}))
    assert outcome.status == "succeeded", outcome.error
    assert built == [("0.1.0", ["slim"], "wynd-base")]


# --- phased (Kubernetes) --------------------------------------------------------------------------------------------

def test_prepare_then_finalize_with_an_external_builder(gated, runs, fakes, no_test_run, monkeypatch, tmp_path):
    ws, commit = gated
    context = tmp_path / "work" / "ctx"
    monkeypatch.setenv("WYND_BUILD_CONTEXT_DIR", str(context))
    registry = MemRegistry({"registries": {"local": {"url": "localhost:5001/wynd"}}})
    inputs = {"process": "gated", "registry": "local", "push": True, "platform": ARM}
    ctx = build_ctx(ws, runs, registry, inputs)
    prepared = job.prepare_build_job(ctx)
    remote = f"localhost:5001/wynd/gated:{commit[:12]}"
    assert (prepared.context_dir, prepared.image_ref) == (str(context), remote)
    assert (context / "image.ref").read_text() == remote + "\n"
    assert {"Dockerfile", "process.lock.yaml", "process.env.yaml", "dist", "venvs"} <= {
        p.name for p in context.iterdir()}

    # the harness stores the JSON between the phases; rootless BuildKit leaves its metadata in the context
    stored = job.Prepared.model_validate_json(prepared.model_dump_json()).model_dump(mode="json")
    (context / "buildkit-metadata.json").write_text(f'{{"containerimage.digest": "{PUSH_DIGEST}"}}')
    ctx.job.artefacts = {"prepared": stored}
    outcome = job.finalize_build_job(ctx)
    assert outcome.status == "succeeded"
    assert (outcome.artefacts["image"], outcome.artefacts["image_digest"], outcome.artefacts["pushed"]) == (
        remote, PUSH_DIGEST, True)
    assert outcome.artefacts["sizes"]["image_bytes"] is None
    build_dir = Path(outcome.artefacts["build_dir"])
    assert not (build_dir / "image.ref").exists() and not (build_dir / "buildkit-metadata.json").exists()
    assert fakes[1].calls == []                             # no docker in the phased path


def test_native_platform_follows_the_host(monkeypatch):
    monkeypatch.setattr(job.host, "machine", lambda: "x86_64")
    assert job.native_platform() == "linux/amd64"
    monkeypatch.setattr(job.host, "machine", lambda: "arm64")
    assert job.native_platform() == "linux/arm64"
    monkeypatch.setattr(job.host, "machine", lambda: "riscv64")
    with pytest.raises(job.BuildFailed, match="riscv64"):
        job.native_platform()


def test_an_editable_runtime_resolves_against_freshly_built_wheels(gated, runs, fakes, no_test_run, monkeypatch):
    ws, _ = gated
    resolver, _ = fakes
    editable = RuntimeSource("0.1.0", (Path("/src/spec"), Path("/src/runtime")))
    monkeypatch.setattr(job, "detect_runtime_source", lambda environ: editable)
    step_wheel = resolver.build_wheel

    def build_wheel(src, out):
        if str(src).startswith("/src"):                     # spec/runtime: only recorded
            resolver.calls.append({"op": "build_wheel", "src": str(src)})
            return Path(out) / "runtime.whl"
        return step_wheel(src, out)

    monkeypatch.setattr(resolver, "build_wheel", build_wheel)
    ctx = build_ctx(ws, runs, MemRegistry(), {"process": "gated", "platform": ARM})
    job.prepare_build_job(ctx)
    assert [c["src"] for c in resolver.calls if c["op"] == "build_wheel"][:2] == ["/src/spec", "/src/runtime"]
    links = {tuple(c["find_links"]) for c in resolver.calls if c["op"] == "compile"}
    assert links == {(str(ctx.scratch / "runtime-wheels"),)}


# --- the dogfood ----------------------------------------------------------------------------------------------------

def test_the_dogfood_builds_three_venvs_and_six_wheels(make_repo, repo_root, runs, fakes, no_test_run):
    pid = "process_supplier_invoice"
    ws = make_repo(source=repo_root / "examples" / "invoices")
    commit = record_passing(ws, runs, pid)
    ctx = build_ctx(ws, runs, MemRegistry(), {"process": pid, "registry": None, "push": False, "platform": ARM})
    outcome = job.run_build_job(ctx)
    assert outcome.status == "succeeded", outcome.error
    build_dir = Path(outcome.artefacts["build_dir"])
    lock = load_process_lock(build_dir / "process.lock.yaml")
    by_venv = {v.id: sorted(s.split("#")[1] for s in v.steps) for v in lock.plan.venvs}
    assert sorted(by_venv.values()) == [["escalate_to_human", "save_record", "validate_fields"],
                                        ["extract_invoice_fields", "fix_fields"], ["read_pdf"]]
    inputs = {v.id: v.inputs for v in lock.venvs}
    assert inputs[lock.plan.steps[f"{pid}#read_pdf"].venv] == ["pypdf==6.19.0"]          # locked_deps win
    assert inputs[lock.plan.steps[f"{pid}#fix_fields"].venv] == ["claude-agent-sdk>=0.2.157,<0.3"]
    assert len(lock.wheels) == 6 and lock.plan.edge_venvs == {}
    assert [f.source for f in lock.fragments][-1] == "provider:claude-code"
    assert (lock.base.variant, lock.base.reason, lock.commit) == ("slim", None, commit)
    dockerfile = (build_dir / "Dockerfile").read_text()
    assert dockerfile.count("uv venv --python") == 3 and "apt-get" not in dockerfile
    assert f'dev.wynd.commit="{commit}"' in dockerfile
    label = next(line for line in dockerfile.splitlines() if line.startswith("LABEL dev.wynd.env-manifest="))
    assert "CLAUDE_CODE_OAUTH_TOKEN" in label and "RECORDS_DIR" in label
    assert outcome.artefacts["image"] == f"wynd/{pid}:{commit[:12]}"

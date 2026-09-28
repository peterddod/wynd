"""`wynd build`, `bake`, `base build|publish` and `serve` (PLAN §9, §3.22; `$DRAFTS/06 §9.2`; CLI-M2).

Build and bake run over a real `JobService` and in-process runner whose `build`/`bake` handlers are the fakes below
(this module is registered as `cli_m2_handlers` so the runner can import them by name). `base` is faked at
`wynd.controller.base`; serving at `ctl.serve` and `wynd.controller.docker.logs_follow` (CTL-M2 owns the real ones).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wynd.controller.errors import EnvMissing, VersionMismatch
from wynd.controller.models import ServedContainer
from wynd.process.jobs import JobContext, JobOutcome

HANDLERS = "cli_m2_handlers"
PYZ = b"PK\x05\x06" + bytes(18)


# --- fake job handlers (run by the in-process runner) ------------------------------------------------------------------

def build_ok(ctx: JobContext) -> JobOutcome:
    pid, ref = ctx.inputs["process"], ctx.job.ref
    ctx.log(f"building {pid}")
    build_dir = ctx.state_dir / "build" / pid / ref
    build_dir.mkdir(parents=True, exist_ok=True)
    pushed = bool(ctx.inputs["push"])
    return JobOutcome(status="succeeded", artefacts={
        "commit": ref, "image": f"wynd/{pid}:{ref[:12]}", "image_digest": "sha256:feed" if pushed else None,
        "build_dir": str(build_dir), "pushed": pushed,
        "tests": {"passed": 5, "failed": 0, "total": 5, "source": ctx.inputs.get("tests_source", "registry")},
    }, report={"inputs": ctx.inputs})


def job_failed(ctx: JobContext) -> JobOutcome:
    ctx.log("running the tests")
    return JobOutcome(status="failed", error=f"tests fail on {ctx.job.ref[:12]}")


def bake_ok(ctx: JobContext) -> JobOutcome:
    pid = ctx.inputs["process"]
    path = ctx.state_dir / "build" / pid / ctx.job.ref / f"{pid}.pyz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PYZ)
    return JobOutcome(status="succeeded", artefacts={"path": str(path), "size_bytes": len(PYZ),
                                                      "commit": ctx.job.ref})


@pytest.fixture
def handlers(monkeypatch, job_handlers):
    monkeypatch.setitem(sys.modules, HANDLERS, sys.modules[__name__])
    return {**job_handlers, "build": f"{HANDLERS}:build_ok", "bake": f"{HANDLERS}:bake_ok"}


@pytest.fixture
def ctl(workspace, make_controller, use_controller, handlers):
    return use_controller(make_controller(workspace, handlers=handlers))


@pytest.fixture
def failing_ctl(workspace, make_controller, use_controller, handlers):
    failing = {**handlers, "build": f"{HANDLERS}:job_failed", "bake": f"{HANDLERS}:job_failed"}
    return use_controller(make_controller(workspace, handlers=failing))


def head(git, ws: Path) -> str:
    return git(ws, "rev-parse", "HEAD").strip()


def only_job(ctl):
    jobs = ctl.jobs.list()
    assert len(jobs) == 1
    return jobs[0]


# --- build ------------------------------------------------------------------------------------------------------------

def test_build_waits_and_prints_the_image(ctl, workspace, cli, git):
    sha = head(git, workspace)
    result = cli("build", "p1")
    assert result.stdout.splitlines() == [
        f"built p1@{sha[:7]}",
        f"image wynd/p1:{sha[:12]}",
        "tests 5/5 (reused recorded result)",
        f"artefacts .wynd/build/p1/{sha}/",
    ]
    job = only_job(ctl)
    assert result.stderr.startswith(f"submitted {job.id} (build) at {sha[:7]}\n")
    assert "building p1" in result.stderr
    assert (job.kind, job.status, job.ref) == ("build", "succeeded", sha)
    assert job.report["inputs"] == {"process": "p1", "registry": None, "push": False}


def test_build_passes_registry_push_and_platform_and_prints_the_digest(ctl, cli):
    result = cli("build", "p1", "--registry", "ghcr", "--push", "--platform", "linux/arm64")
    assert "digest sha256:feed" in result.stdout.splitlines()
    assert only_job(ctl).report["inputs"] == {"process": "p1", "registry": "ghcr", "push": True,
                                              "platform": "linux/arm64"}


def test_build_pushes_to_the_default_registry_unless_no_push(ctl, cli):
    ctl.ctx.stores.registry.put("registries", "local", {"name": "local", "url": "localhost:5001/wynd",
                                                        "default": True})
    cli("build", "p1")
    cli("build", "p1", "--no-push")
    inputs = [job.report["inputs"] for job in reversed(ctl.jobs.list())]
    assert [(i["registry"], i["push"]) for i in inputs] == [("local", True), ("local", False)]


def test_build_reports_tests_that_ran(ctl, cli, monkeypatch):
    original = ctl.jobs.submit

    def submit(kind, pid, inputs=None):
        return original(kind, pid, {**(inputs or {}), "tests_source": "ran"})

    monkeypatch.setattr(ctl.jobs, "submit", submit)
    assert "tests 5/5 (ran)" in cli("build", "p1").stdout.splitlines()


def test_build_json_is_one_document(ctl, workspace, cli, git):
    doc = json.loads(cli("build", "p1", "--json").stdout)
    sha = head(git, workspace)
    assert doc["kind"] == "build" and doc["status"] == "succeeded"
    assert doc["build"]["image"] == f"wynd/p1:{sha[:12]}" and doc["build"]["tests"]["source"] == "registry"


def test_build_no_wait_prints_the_job_id(ctl, cli):
    result = cli("build", "p1", "--no-wait")
    job_id = result.stdout.strip()
    assert job_id.startswith("job_") and result.stderr.startswith(f"submitted {job_id} (build)")
    assert ctl.jobs.wait(job_id, poll=0.02).status == "succeeded"


def test_a_failed_build_exits_1(failing_ctl, workspace, cli, git):
    result = cli("build", "p1", code=1)
    assert result.stdout == ""
    job = only_job(failing_ctl)
    assert f"job {job.id} failed: tests fail on {head(git, workspace)[:12]}" in result.stderr


def test_a_failed_build_json_still_prints_the_job(failing_ctl, cli):
    doc = json.loads(cli("build", "p1", "--json", code=1).stdout)
    assert doc["status"] == "failed" and doc["error"]["message"].startswith("tests fail on ")


def test_build_refuses_a_dirty_workspace(ctl, workspace, cli, write_files):
    write_files(workspace, {"processes/p1/notes.md": "dirty\n"})
    result = cli("build", "p1", code=3)
    assert "error: the workspace has uncommitted changes" in result.stderr
    assert "  processes/p1/notes.md" in result.stderr.splitlines()
    assert ctl.jobs.list() == []


def test_build_of_an_unknown_process_exits_3(ctl, cli):
    assert "unknown process 'nope'" in cli("build", "nope", code=3).stderr


# --- bake -------------------------------------------------------------------------------------------------------------

def test_bake_prints_the_artefact(ctl, workspace, cli, git):
    sha = head(git, workspace)
    path = workspace / ".wynd" / "build" / "p1" / sha / "p1.pyz"
    result = cli("bake", "p1")
    assert result.stdout.splitlines() == [f"baked p1@{sha[:7]}", f"artefact {path} ({len(PYZ)} bytes)"]
    assert only_job(ctl).kind == "bake"


def test_bake_copies_to_output(ctl, cli, tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.chdir(out)
    result = cli("bake", "p1", "--output", "app.pyz")
    assert (out / "app.pyz").read_bytes() == PYZ
    assert result.stdout.splitlines()[-1] == f"copied to {out / 'app.pyz'}"
    cli("bake", "p1", "--output", str(out))
    assert (out / "p1.pyz").read_bytes() == PYZ


def test_bake_json(ctl, cli, tmp_path):
    doc = json.loads(cli("bake", "p1", "--json", "--output", str(tmp_path / "x.pyz")).stdout)
    assert doc["kind"] == "bake" and doc["artefacts"]["size_bytes"] == len(PYZ)
    assert (tmp_path / "x.pyz").read_bytes() == PYZ


def test_a_failed_bake_exits_1_and_copies_nothing(failing_ctl, cli, tmp_path):
    result = cli("bake", "p1", "--output", str(tmp_path / "x.pyz"), code=1)
    assert "failed: tests fail on" in result.stderr and not (tmp_path / "x.pyz").exists()


# --- base -------------------------------------------------------------------------------------------------------------

class FakeBase:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.error: Exception | None = None

    def build_base(self, version, variants, *, log):
        return self._call("build", version, variants, None, log)

    def publish_base(self, version, variants, registry_name, *, log):
        return self._call("publish", version, variants, registry_name, log)

    def _call(self, op, version, variants, registry, log):
        self.calls.append((op, version, list(variants), registry))
        if self.error is not None:
            raise self.error
        log(f"{op}ing {version}")
        host = f"{registry}/" if registry else ""
        return [f"{host}wynd-base:{version}-{variant}" for variant in variants]


@pytest.fixture
def base(monkeypatch) -> FakeBase:
    fake = FakeBase()
    monkeypatch.setattr("wynd.controller.base.build_base", fake.build_base)
    monkeypatch.setattr("wynd.controller.base.publish_base", fake.publish_base)

    def no_controller(ctx):
        raise AssertionError("base commands need no workspace")

    monkeypatch.setattr("wynd.cli.context.get_controller", no_controller)
    return fake


def test_base_build_defaults_to_every_variant(base, cli):
    result = cli("base", "build", "0.1.0")
    assert result.stdout.splitlines() == ["wynd-base:0.1.0-slim", "wynd-base:0.1.0-alpine"]
    assert "building 0.1.0" in result.stderr
    assert base.calls == [("build", "0.1.0", ["slim", "alpine"], None)]


def test_base_build_one_variant_as_json(base, cli):
    doc = json.loads(cli("base", "build", "0.1.0", "--variant", "slim", "--json").stdout)
    assert doc == {"items": ["wynd-base:0.1.0-slim"]}
    assert base.calls == [("build", "0.1.0", ["slim"], None)]


def test_base_rejects_an_unknown_variant(base, cli):
    assert "--variant must be one of slim, alpine, all" in cli("base", "build", "0.1.0", "--variant", "arch",
                                                               code=2).stderr
    assert base.calls == []


def test_base_version_mismatch_exits_3(base, cli):
    base.error = VersionMismatch("base version 9.9.9 != runtime version 0.1.0")
    assert "error: base version 9.9.9" in cli("base", "build", "9.9.9", code=3).stderr


def test_base_publish_pushes_to_the_named_registry(base, cli):
    result = cli("base", "publish", "0.1.0", "--registry", "ghcr", "--variant", "alpine")
    assert result.stdout.splitlines() == ["ghcr/wynd-base:0.1.0-alpine"]
    assert base.calls == [("publish", "0.1.0", ["alpine"], "ghcr")]


def test_base_publish_needs_a_registry(base, cli):
    cli("base", "publish", "0.1.0", code=2)
    assert base.calls == []


# --- serve ------------------------------------------------------------------------------------------------------------

IMAGE = "wynd/p1:0123456789ab"
NAME = "wynd-p1-0123456"


class FakeServe:
    def __init__(self) -> None:
        self.served: list[tuple[str, dict]] = []
        self.stopped: list[str] = []
        self.error: Exception | None = None
        self.running: list[str] = []

    def serve(self, image, *, port=None, extra_env_files=(), timeout=120.0, register=True, name=None):
        self.served.append((image, {"port": port, "extra_env_files": list(extra_env_files), "timeout": timeout,
                                    "register": register, "name": name}))
        if self.error is not None:
            raise self.error
        container = name or NAME
        self.running.append(container)
        return ServedContainer(name=container, container_id="c0ffee", image=image, url="http://127.0.0.1:53817",
                               process="p1", commit="0123456789abcdef", started_at=datetime(2026, 9, 27, tzinfo=UTC))

    def stop(self, name_or_image):
        self.stopped.append(name_or_image)
        matched = [n for n in self.running if name_or_image in (n, IMAGE)]
        self.running = [n for n in self.running if n not in matched]
        return matched


class FakeLogs:
    """A `docker logs -f` process: `lines` on stdout, then `wait` ends it or raises `interrupt`."""

    def __init__(self, lines: list[str], interrupt: bool) -> None:
        self.stdout = iter(lines)
        self.interrupt = interrupt
        self.terminated = False

    def wait(self):
        if self.interrupt:
            raise KeyboardInterrupt
        return 0

    def terminate(self):
        self.terminated = True


@pytest.fixture
def serving(ctl, monkeypatch) -> FakeServe:
    fake = FakeServe()
    monkeypatch.setattr(ctl, "serve", fake)
    return fake


@pytest.fixture
def logs(monkeypatch):
    made: list[FakeLogs] = []

    def follow(interrupt: bool, lines: list[str]):
        def logs_follow(name):
            assert name == NAME
            made.append(FakeLogs(lines, interrupt))
            return made[-1]

        monkeypatch.setattr("wynd.controller.docker.logs_follow", logs_follow)
        return made

    return follow


def test_serve_detached_prints_the_url_and_leaves_it_running(serving, cli, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = cli("serve", IMAGE, "-d", "--port", "9000", "--env-file", ".env.prod", "--env-file", "/abs/x.env",
                 "--timeout", "30")
    assert result.stdout == f"serving p1@0123456 at http://127.0.0.1:53817 (container {NAME})\n"
    assert serving.served == [(IMAGE, {"port": 9000, "extra_env_files": [tmp_path / ".env.prod", Path("/abs/x.env")],
                                       "timeout": 30.0, "register": True, "name": None})]
    assert serving.running == [NAME] and serving.stopped == []


def test_serve_json(serving, cli):
    doc = json.loads(cli("serve", IMAGE, "--detach", "--name", "mine", "--json").stdout)
    assert doc["name"] == "mine" and doc["url"] == "http://127.0.0.1:53817" and doc["process"] == "p1"


def test_serve_follows_the_log_until_ctrl_c_then_stops(serving, logs, cli):
    made = logs(True, ["starting\n", b"ready\n"])
    result = cli("serve", IMAGE)
    assert result.stdout.startswith("serving p1@0123456")
    assert result.stderr.splitlines() == ["starting", "ready", f"stopped {NAME}"]
    assert made[0].terminated and serving.stopped == [NAME] and serving.running == []


def test_serve_exits_1_when_the_container_stops_on_its_own(serving, logs, cli):
    logs(False, ["crashed\n"])
    result = cli("serve", IMAGE, code=1)
    assert result.stderr.splitlines() == ["crashed", f"container {NAME} stopped serving"]
    assert serving.stopped == [NAME]


def test_serve_env_missing_exits_3(serving, cli):
    serving.error = EnvMissing("missing required variables: CLAUDE_CODE_OAUTH_TOKEN", hint="set them in .env")
    result = cli("serve", IMAGE, "-d", code=3)
    assert "error: missing required variables: CLAUDE_CODE_OAUTH_TOKEN" in result.stderr
    assert "hint: set them in .env" in result.stderr


def test_serve_stop(serving, cli):
    cli("serve", IMAGE, "-d")
    assert cli("serve", IMAGE, "--stop").stdout == f"stopped {NAME}\n"
    assert serving.served[0][0] == IMAGE and len(serving.served) == 1 and serving.stopped == [IMAGE]


def test_serve_stop_json(serving, cli):
    cli("serve", IMAGE, "-d")
    assert json.loads(cli("serve", NAME, "--stop", "--json").stdout) == {"items": [NAME]}


def test_serve_stop_of_nothing_exits_3(serving, cli):
    assert "error: no served container matches 'nothing'" in cli("serve", "nothing", "--stop", code=3).stderr


# --- serve outside a workspace ($DRAFTS/06 §9.2: nothing registered, no .env read) --------------------------------------

class FakeDocker:
    """`wynd.controller.docker` for a served image: records the containers run and removed."""

    def __init__(self, labels: dict[str, str]) -> None:
        self.labels = labels
        self.runs: list[dict] = []
        self.removed: list[str] = []

    def image_labels(self, image):
        return self.labels

    def run_detached(self, image, *, name, env, host_port, container_port, labels, restart=None):
        self.runs.append({"image": image, "name": name, "env": dict(env), "host_port": host_port})
        return "c0ffee"

    def host_port(self, name, container_port):
        return 53817

    def rm(self, name):
        self.removed.append(name)


@pytest.fixture
def outside(tmp_path, monkeypatch):
    """cwd is a directory with a `.env` but no `wynd.yaml`; Docker and run-API readiness are faked."""
    from wynd.runtime.supervisor.client import RunApiClient
    from wynd.spec.env_manifest import EnvManifest
    from wynd.spec.fragments import EnvVar

    manifest = EnvManifest(process="p1", commit="0123456789abcdef", vars=[
        EnvVar(name="RECORDS_DIR"), EnvVar(name="API_KEY", secret=True, required=False)])
    fake = FakeDocker({"dev.wynd.commit": "0123456789abcdef", "dev.wynd.env-manifest": manifest.model_dump_json()})
    for attr in ("image_labels", "run_detached", "host_port", "rm"):
        monkeypatch.setattr(f"wynd.controller.docker.{attr}", getattr(fake, attr))
    monkeypatch.setattr(RunApiClient, "wait_ready", lambda self, timeout=120.0, interval=0.25: None)
    monkeypatch.delenv("WYND_WORKSPACE", raising=False)
    monkeypatch.delenv("RECORDS_DIR", raising=False)
    monkeypatch.setenv("API_KEY", "from-environ")
    (tmp_path / ".env").write_text("RECORDS_DIR=/from/dotenv\n")
    (tmp_path / "prod.env").write_text("RECORDS_DIR=/data/records\nAPI_KEY=from-file\n")
    monkeypatch.chdir(tmp_path)
    return fake


def test_serve_outside_a_workspace_uses_environ_and_env_files_only(outside, cli, tmp_path):
    result = cli("serve", IMAGE, "-d", "--env-file", "prod.env")
    assert result.stdout == f"serving p1@0123456 at http://127.0.0.1:53817 (container {NAME})\n"
    assert outside.runs == [{"image": IMAGE, "name": NAME, "host_port": None,
                             "env": {"API_KEY": "from-environ", "RECORDS_DIR": "/data/records"}}]
    assert not (tmp_path / ".wynd").exists()


def test_serve_outside_a_workspace_ignores_the_cwd_dotenv(outside, cli):
    result = cli("serve", IMAGE, "-d", code=3)
    assert "RECORDS_DIR" in result.stderr and outside.runs == []


def test_serve_outside_a_workspace_follows_then_removes_the_container(outside, logs, cli):
    made = logs(True, ["ready\n"])
    result = cli("serve", IMAGE, "--env-file", "prod.env")
    assert result.stderr.splitlines() == ["ready", f"stopped {NAME}"]
    assert made[0].terminated and outside.removed == [NAME, NAME]   # before the run, then on Ctrl-C


def test_serve_stop_outside_a_workspace_matches_nothing(outside, cli):
    result = cli("serve", NAME, "--stop", code=3)
    assert f"error: no served container matches {NAME!r}" in result.stderr
    assert "outside a workspace nothing is registered" in result.stderr and outside.removed == []


def test_serve_with_an_explicit_non_workspace_still_exits_3(outside, cli, tmp_path):
    result = cli("-C", tmp_path, "serve", IMAGE, "-d", "--env-file", "prod.env", code=3)
    assert result.stderr.startswith("error: ") and outside.runs == []

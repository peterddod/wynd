"""`ServeService` and the Docker CLI helpers (CTL-M2; PLAN §8.1, `$DRAFTS/06 §5.12`, §11.2 `test_serving.py`)."""

from __future__ import annotations

import json
import subprocess

import pytest

from support.ctl_m2_runapi import FakeDocker, FakeRunApi
from wynd.controller import docker
from wynd.controller.errors import EnvMissing, Invalid, NotFound, Unavailable
from wynd.controller.models import ServedContainer
from wynd.controller.serving import ServeService
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.fragments import EnvVar

IMAGE = "wynd/p1:0123456789ab"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
ENV = {"RECORDS_DIR": "/data/records", "WYND_RUN_API_TOKEN": "tok-1", "UNRELATED_SECRET": "s3"}


def labels(pid: str = "p1", commit: str = COMMIT, vars: list[EnvVar] | None = None) -> dict[str, str]:
    if vars is None:
        vars = [EnvVar(name="RECORDS_DIR", description="Records."),
                EnvVar(name="WYND_RUN_API_TOKEN", secret=True, required=False, used_by=["runtime"])]
    manifest = EnvManifest(process=pid, commit=commit, vars=vars)
    return {"dev.wynd.process": pid, "dev.wynd.commit": commit, "dev.wynd.run-api-port": "8080",
            "dev.wynd.env-manifest": manifest.model_dump_json()}


@pytest.fixture
def api() -> FakeRunApi:
    return FakeRunApi()


@pytest.fixture
def fake_docker(api, monkeypatch) -> FakeDocker:
    return FakeDocker(api, {IMAGE: labels()}).install(monkeypatch)


@pytest.fixture
def serving_controller(workspace, make_controller, api):
    def make(env: dict[str, str | None] | None = None, root=None):
        ctl = make_controller(root or workspace, env=ENV if env is None else env)
        ctl.serve = ServeService(ctl.ctx, ctl, run_api_client=api)
        return ctl

    return make


def test_serve_passes_manifest_env_names_and_records_the_container(serving_controller, fake_docker, api):
    ctl = serving_controller()
    served = ctl.serve.serve(IMAGE)

    assert served.name == f"wynd-p1-{COMMIT[:7]}"
    assert (served.process, served.commit, served.image) == ("p1", COMMIT, IMAGE)
    [started] = fake_docker.started()
    assert started["env"] == {"RECORDS_DIR": "/data/records", "WYND_RUN_API_TOKEN": "tok-1"}   # no UNRELATED_SECRET
    assert started["labels"] == {"dev.wynd.served": "1"}
    assert started["container_port"] == 8080
    assert started["host_port"] is None
    assert served.url == f"http://127.0.0.1:{fake_docker.ports[served.name]}"
    assert api.clients[-1].token == "tok-1"
    record = ctl.ctx.state_dir / "serve" / f"{served.name}.json"
    stored = ServedContainer.model_validate_json(record.read_text())
    assert stored == served
    assert stored.started_at == ctl.ctx.clock() and stored.stopped_at is None
    assert "tok-1" not in record.read_text()
    assert ctl.serve.list() == [served]


def test_the_controllers_storage_and_bind_settings_never_reach_the_container(serving_controller, fake_docker):
    from wynd.runtime.storage import STORAGE_ENV
    from wynd.runtime.supervisor.schema import SUPERVISOR_ENV

    fake_docker.labels[IMAGE] = labels(vars=[EnvVar(name="RECORDS_DIR"), *STORAGE_ENV, *SUPERVISOR_ENV])
    host = {"WYND_HOME": "/Users/me/.wynd", "WYND_REGISTRY": "file", "WYND_DATA_DIR": "/Users/me/data",
            "WYND_HOST": "127.0.0.1", "WYND_PORT": "9999", "WYND_MAX_CONCURRENT_RUNS": "2"}
    serving_controller(env={**ENV, **host}).serve.serve(IMAGE)
    assert fake_docker.started()[0]["env"] == {"RECORDS_DIR": "/data/records", "WYND_MAX_CONCURRENT_RUNS": "2",
                                               "WYND_RUN_API_TOKEN": "tok-1"}


def test_serve_with_an_explicit_port_and_name(serving_controller, fake_docker):
    served = serving_controller().serve.serve(IMAGE, port=18080, name="mine")
    assert served.name == "mine"
    assert served.url == "http://127.0.0.1:18080"
    assert fake_docker.started()[0]["host_port"] == 18080


def test_serve_reads_extra_env_files(serving_controller, fake_docker, tmp_path):
    env_file = tmp_path / "extra.env"
    env_file.write_text("RECORDS_DIR=/from/file\n")
    ctl = serving_controller(env={"RECORDS_DIR": None, "WYND_RUN_API_TOKEN": None})
    ctl.serve.serve(IMAGE, extra_env_files=[env_file])
    assert fake_docker.started()[0]["env"] == {"RECORDS_DIR": "/from/file"}


def test_serve_refuses_missing_required_env_before_docker_run(serving_controller, fake_docker):
    ctl = serving_controller(env={"RECORDS_DIR": None})
    with pytest.raises(EnvMissing) as caught:
        ctl.serve.serve(IMAGE)
    assert caught.value.details["missing"] == ["RECORDS_DIR"]
    assert fake_docker.started() == []
    assert ctl.serve.list() == []


def test_serve_refuses_an_image_without_a_manifest_label(serving_controller, fake_docker):
    fake_docker.labels["alpine:3"] = {}
    with pytest.raises(Invalid, match="not a wynd process image"):
        serving_controller().serve.serve("alpine:3")


def test_serve_timeout_is_unavailable_with_the_log_tail(serving_controller, fake_docker):
    fake_docker.becomes_ready = False
    ctl = serving_controller()
    with pytest.raises(Unavailable) as caught:
        ctl.serve.serve(IMAGE, timeout=0.1)
    assert "env-check failed: RECORDS_DIR missing" in caught.value.message
    assert f"wynd-p1-{COMMIT[:7]}" in fake_docker.removed()
    assert ctl.serve.list() == []


def test_current_registry_snapshot_reaches_the_container(make_workspace, agentic_files, serving_controller,
                                                         fake_docker):
    """The image's manifest was built before the MCP entry existed; the container still gets the current snapshot."""
    ws = make_workspace(files=agentic_files("p4", mcp="github"))
    image = "wynd/p4:0123456789ab"
    fake_docker.labels[image] = labels("p4", vars=[])
    ctl = serving_controller(root=ws)
    entry = {"name": "github", "transport": "http", "url": "https://mcp.example/mcp"}
    ctl.ctx.stores.registry.put("mcp", "github", entry)

    ctl.serve.serve(image)

    env = fake_docker.started()[0]["env"]
    assert json.loads(env["WYND_REGISTRY_JSON"])["mcp"]["github"]["url"] == "https://mcp.example/mcp"
    assert set(env) == {"WYND_REGISTRY_JSON", "WYND_RUN_API_TOKEN"}


def test_no_registry_json_without_a_snapshot(serving_controller, fake_docker):
    serving_controller().serve.serve(IMAGE)
    assert "WYND_REGISTRY_JSON" not in fake_docker.started()[0]["env"]


def test_acquire_reuses_a_ready_container_over_http_only(serving_controller, fake_docker):
    ctl = serving_controller()
    served = ctl.serve.serve(IMAGE)
    fake_docker.calls.clear()

    assert ctl.serve.acquire(IMAGE) == (served.url, False)
    assert fake_docker.calls == []


def test_acquire_starts_an_ephemeral_container_when_none_is_ready(serving_controller, fake_docker, api):
    ctl = serving_controller()
    served = ctl.serve.serve(IMAGE)
    api.ready.clear()                                      # the warm container died

    url, ephemeral = ctl.serve.acquire(IMAGE)

    assert ephemeral is True and url != served.url
    name = ctl.serve.ephemeral[url]
    assert name.startswith("wynd-run-") and len(name) == len("wynd-run-") + 8
    assert ctl.serve.list() == []                          # the dead one is marked stopped, the ephemeral unrecorded
    old = ServedContainer.model_validate_json((ctl.ctx.state_dir / "serve" / f"{served.name}.json").read_text())
    assert old.stopped_at is not None and old.state == "stopped"
    assert not (ctl.ctx.state_dir / "serve" / f"{name}.json").exists()

    assert ctl.serve.stop(name) == [name]
    assert fake_docker.removed()[-1] == name
    assert ctl.serve.ephemeral == {}


def test_acquire_ignores_release_containers(serving_controller, fake_docker):
    ctl = serving_controller()
    served = ctl.serve.serve(IMAGE, name="wynd-rel-rel_1")
    record = ctl.ctx.state_dir / "serve" / f"{served.name}.json"
    record.write_text(served.model_copy(update={"release_id": "rel_1"}).model_dump_json())

    url, ephemeral = ctl.serve.acquire(IMAGE)
    assert ephemeral is True and url != served.url
    assert ctl.serve.stop(IMAGE) == []                     # release containers stop only by name
    assert ctl.serve.stop("wynd-rel-rel_1") == ["wynd-rel-rel_1"]


def test_stop_by_image_keeps_the_record_with_its_uptime(serving_controller, fake_docker):
    ctl = serving_controller()
    served = ctl.serve.serve(IMAGE)
    ctl.ctx.clock.advance(90)

    assert ctl.serve.stop(IMAGE) == [served.name]
    assert fake_docker.removed()[-1] == served.name
    assert ctl.serve.list() == []
    stored = ServedContainer.model_validate_json((ctl.ctx.state_dir / "serve" / f"{served.name}.json").read_text())
    assert (stored.stopped_at - stored.started_at).total_seconds() == 90
    assert ctl.serve.stop(IMAGE) == []


def test_serving_again_replaces_the_container_of_the_same_name(serving_controller, fake_docker):
    ctl = serving_controller()
    first = ctl.serve.serve(IMAGE)
    second = ctl.serve.serve(IMAGE)
    assert first.name == second.name
    assert fake_docker.removed().count(first.name) == 2    # before each start (idempotent rm)
    assert ctl.serve.list() == [second]


# --- docker.py over a recorded subprocess -------------------------------------------------------------------------------

class Recorder:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "", raises: Exception | None = None):
        self.result = (returncode, stdout, stderr)
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, argv, **kw):
        self.calls.append({"argv": argv, **kw})
        if self.raises is not None:
            raise self.raises
        code, out, err = self.result
        return subprocess.CompletedProcess(argv, code, out, err)


def test_run_detached_passes_env_names_in_argv_and_values_in_the_child_env(monkeypatch):
    rec = Recorder(stdout="e3b0c442\n")
    monkeypatch.setattr(docker.subprocess, "run", rec)

    cid = docker.run_detached(IMAGE, name="wynd-p1-0123456", env={"SECRET_TOKEN": "s3cr3t-value", "RECORDS_DIR": "/r"},
                              host_port=None, container_port=8080, labels={"dev.wynd.served": "1"})

    assert cid == "e3b0c442"
    argv = rec.calls[0]["argv"]
    assert argv[:5] == ["docker", "run", "-d", "--name", "wynd-p1-0123456"]
    assert argv[argv.index("-p") + 1] == "127.0.0.1::8080"
    assert ["-e", "SECRET_TOKEN"] == argv[argv.index("SECRET_TOKEN") - 1: argv.index("SECRET_TOKEN") + 1]
    assert ["-e", "RECORDS_DIR"] == argv[argv.index("RECORDS_DIR") - 1: argv.index("RECORDS_DIR") + 1]
    assert not any("s3cr3t" in arg or "=/r" in arg for arg in argv)
    assert argv[argv.index("--label") + 1] == "dev.wynd.served=1"
    assert argv[-1] == IMAGE and "-v" not in argv and "--mount" not in argv
    assert rec.calls[0]["env"]["SECRET_TOKEN"] == "s3cr3t-value"


def test_run_detached_with_host_port_and_restart(monkeypatch):
    rec = Recorder(stdout="id\n")
    monkeypatch.setattr(docker.subprocess, "run", rec)
    docker.run_detached(IMAGE, name="n", env={}, host_port=9000, container_port=8080, labels={}, restart="always")
    argv = rec.calls[0]["argv"]
    assert argv[argv.index("-p") + 1] == "127.0.0.1:9000:8080"
    assert argv[argv.index("--restart") + 1] == "always"


def test_missing_docker_binary_is_unavailable(monkeypatch):
    monkeypatch.setattr(docker.subprocess, "run", Recorder(raises=FileNotFoundError("docker")))
    with pytest.raises(Unavailable, match="not installed"):
        docker.image_labels(IMAGE)


def test_daemon_down_is_unavailable(monkeypatch):
    stderr = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
    monkeypatch.setattr(docker.subprocess, "run", Recorder(returncode=1, stderr=stderr))
    with pytest.raises(Unavailable, match="not reachable"):
        docker.rm("x")


def test_image_labels(monkeypatch):
    monkeypatch.setattr(docker.subprocess, "run", Recorder(stdout=json.dumps(labels()) + "\n"))
    assert docker.image_labels(IMAGE) == labels()
    monkeypatch.setattr(docker.subprocess, "run", Recorder(stdout="null\n"))
    assert docker.image_labels(IMAGE) == {}


def test_image_labels_of_a_missing_image_is_not_found(monkeypatch):
    monkeypatch.setattr(docker.subprocess, "run", Recorder(returncode=1, stderr="Error: No such image: nope:1"))
    with pytest.raises(NotFound, match="image nope:1 not found"):
        docker.image_labels("nope:1")


def test_host_port_parses_the_first_binding(monkeypatch):
    rec = Recorder(stdout="127.0.0.1:53817\n[::1]:53817\n")
    monkeypatch.setattr(docker.subprocess, "run", rec)
    assert docker.host_port("n", 8080) == 53817
    assert rec.calls[0]["argv"] == ["docker", "port", "n", "8080/tcp"]


def test_rm_is_idempotent(monkeypatch):
    rec = Recorder(returncode=1, stderr="Error response from daemon: No such container: gone")
    monkeypatch.setattr(docker.subprocess, "run", rec)
    docker.rm("gone")
    assert rec.calls[0]["argv"] == ["docker", "rm", "-f", "gone"]


def test_logs_tail_merges_streams(monkeypatch):
    rec = Recorder(stdout="out\n", stderr="err\n")
    monkeypatch.setattr(docker.subprocess, "run", rec)
    assert docker.logs_tail("n", 5) == "out\nerr\n"
    assert rec.calls[0]["argv"] == ["docker", "logs", "--tail", "5", "n"]

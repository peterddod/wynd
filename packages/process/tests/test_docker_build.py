"""Real images with Docker (PLAN §6.5; `$DRAFTS/04 §18.5`): the slim base, then a full dogfood build whose test gate
reuses a recorded result. Opt-in: `WYND_DOCKER=1` (needs Docker with buildx and network access for the base images
and PyPI).
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

import pytest
from support.proc_build_fakes import build_ctx
from support.proc_env_workspaces import MemRegistry

from wynd.process import base
from wynd.process.build.imagebuilder import BuildxImageBuilder
from wynd.process.build.job import run_build_job
from wynd.process.git import closure_head, reference_closure
from wynd.process.hashing import process_hash
from wynd.process.workspace import load_workspace
from wynd.runtime.storage import stores_from_env
from wynd.runtime.storage.models import TestResult

pytestmark = pytest.mark.docker

PID = "process_supplier_invoice"
BASE = "wynd-base:0.1.0-slim"


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


@pytest.fixture(scope="module")
def slim_base():
    refs = base.build_base("0.1.0", ["slim"], log=print, image_builder=BuildxImageBuilder())
    assert refs == [BASE]
    return BASE


def test_build_base_slim(slim_base, monkeypatch):
    monkeypatch.delenv("WYND_BASE_REPO", raising=False)
    builder = BuildxImageBuilder()
    assert builder.exists(slim_base)
    base.ensure_base(slim_base, "0.1.0", "slim", image_builder=builder, log=print)      # found: nothing to do
    user = docker("image", "inspect", "--format", "{{.Config.User}}", slim_base).stdout.strip()
    assert user == "wynd"
    out = docker("run", "--rm", "--entrypoint", "sh", slim_base, "-c",
                 "id -u && touch /home/wynd/x /var/lib/wynd/y && ls /opt/wynd/wheels && "
                 "/opt/wynd/supervisor/bin/python -c 'import wynd.runtime, wynd.spec; print(\"ok\")'").stdout
    assert out.splitlines()[0] == "10001" and out.splitlines()[-1] == "ok"


def test_full_dogfood_build(slim_base, make_repo, repo_root, tmp_path, monkeypatch):
    monkeypatch.delenv("WYND_BASE_REPO", raising=False)
    ws = make_repo(source=repo_root / "examples" / "invoices")
    monkeypatch.delenv("UV_OFFLINE", raising=False)                  # host-side resolution needs the index
    (ws / ".env").write_text("CLAUDE_CODE_OAUTH_TOKEN=never-in-an-image\n")
    runs = stores_from_env({"WYND_HOME": str(tmp_path / "home")}, data_dir=tmp_path / "data").runs
    workspace = load_workspace(ws)
    commit = closure_head(ws, reference_closure(workspace, PID))
    key = f"process:{PID}:{process_hash(workspace.tree, workspace.load_process(PID))}"
    runs.put_test_result(TestResult(commit=commit, key=key, passed=True, counts={"passed": 1}, ran_at=datetime.now(UTC)))

    lines: list[str] = []
    outcome = run_build_job(build_ctx(ws, runs, MemRegistry(), {"process": PID, "registry": None, "push": False},
                                      lines=lines))
    assert outcome.status == "succeeded", outcome.error or "\n".join(lines[-40:])
    image = f"wynd/{PID}:{commit[:12]}"
    assert outcome.artefacts["image"] == image
    try:
        assert outcome.artefacts["sizes"]["image_bytes"] > 0
        inspect = json.loads(docker("image", "inspect", image).stdout)[0]["Config"]
        assert inspect["User"] == "wynd"
        labels = inspect["Labels"]
        assert labels["dev.wynd.process"] == PID and labels["dev.wynd.commit"] == commit
        assert labels["dev.wynd.base-variant"] == "slim" and labels["dev.wynd.run-api-port"] == "8080"
        manifest = json.loads(labels["dev.wynd.env-manifest"])
        assert "CLAUDE_CODE_OAUTH_TOKEN" in {var["name"] for var in manifest["vars"]}

        check = docker("run", "--rm", image, "check-plan", "/opt/wynd/process/process.lock.yaml", check=False)
        assert check.returncode == 0, check.stdout + check.stderr
        env_file = docker("run", "--rm", "--entrypoint", "sh", image, "-c",
                          "test ! -e /work/.env && ! grep -rl never-in-an-image /opt/wynd /work", check=False)
        assert env_file.returncode == 0, env_file.stdout + env_file.stderr
    finally:
        docker("image", "rm", "-f", image, check=False)

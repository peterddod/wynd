"""Image mode end to end with real Docker (CTL-M2; PLAN §8.2, §14 M2-INT item 3). Opt-in: `WYND_DOCKER=1`.

Offline for credentials (the `fake` provider stands in for Claude) but not for the network: the build resolves step
dependencies from PyPI and needs the `wynd-base` image (built on demand from the monorepo when missing).

1. The fake-provider dogfood variant is built, served (`wynd serve -d`), and examples 1-5 run with `--image`; each
   run ends with the example's exit and its `(step, exit, outputs)` sequence equals `run --local` of the same copy;
   image runs against the warm container make no Docker call at all.
2. A one-step agentic process using an http MCP server (the runtime's fake server on the host) is built; the registry
   entry reaches the container only through `WYND_REGISTRY_JSON` (+ `WYND_MCP_NOTES_URL` pointing at the host), the
   run succeeds only after the runtime connected and listed the server's tools, and the image refuses to start
   (`env-check`) when `WYND_REGISTRY_JSON` is withheld.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from support.ctl_m2_variant import FAKE_SCRIPT, MCP_PID, PID, PROCESS_REL, build_image, dogfood_copy, mcp_workspace, \
    step_triples
from wynd.controller.controller import Controller
from wynd.controller.models import ImageTarget
from wynd.spec import load_process

pytestmark = pytest.mark.docker

DIRS = ("RECORDS_DIR", "REVIEW_DIR", "ESCALATIONS_DIR")
CONTAINER_OUT = "/tmp/wynd-out"
READY_TIMEOUT = 180.0


def open_controller(ws: Path, env: dict[str, str]) -> Controller:
    return Controller.open(ws, environ={**os.environ, **env}, load_dotenv=False)


def remove_image(image: str) -> None:
    subprocess.run(["docker", "image", "rm", "-f", image], capture_output=True, check=False)


@pytest.fixture
def online(monkeypatch):
    monkeypatch.delenv("UV_OFFLINE", raising=False)                  # host-side resolution needs the index


@pytest.fixture
def docker_calls(monkeypatch) -> list[str]:
    """Every `wynd.controller.docker` call made after the fixture is set up, by function name."""
    import wynd.controller.docker as docker

    calls: list[str] = []
    for name in ("image_labels", "run_detached", "host_port", "rm", "logs_tail", "logs_follow"):
        real = getattr(docker, name)

        def spy(*args, _real=real, _name=name, **kw):
            calls.append(_name)
            return _real(*args, **kw)

        monkeypatch.setattr(docker, name, spy)
    return calls


def test_fake_dogfood_image_runs_match_local_runs(tmp_path, shared_venvs, online, docker_calls):
    ws = dogfood_copy(tmp_path, shared_venvs)
    examples = load_process(ws / PROCESS_REL / "process.yaml").examples
    script = FAKE_SCRIPT.read_text()
    local_out = tmp_path / "out"
    container_env = {name: f"{CONTAINER_OUT}/{name.lower()}" for name in DIRS} | {"WYND_FAKE_PROVIDER_SCRIPT": script}
    local_env = {name: str(local_out / name.lower()) for name in DIRS} | {"WYND_FAKE_PROVIDER_SCRIPT": script}

    image = build_image(open_controller(ws, local_env), PID)
    serving = open_controller(ws, container_env)
    try:
        served = serving.serve.serve(image, timeout=READY_TIMEOUT)
        assert served.process == PID and served.url.startswith("http://127.0.0.1:")
        docker_calls.clear()

        ctl = open_controller(ws, local_env)
        walls = []
        for n, example in enumerate(examples, start=1):
            pdf = str(ws / PROCESS_REL / example.inputs["pdf_path"])
            local = ctl.runs.run(PID, {"pdf_path": pdf})
            started = time.monotonic()
            remote = ctl.runs.run(PID, {"pdf_path": pdf}, target=ImageTarget())
            walls.append(time.monotonic() - started)

            assert (local.exit, remote.exit) == (example.exit, example.exit), f"example {n}"
            assert remote.mode == "image" and remote.status == "succeeded"
            replace = {str(local_out): "<out>", CONTAINER_OUT: "<out>", local.id: "<run>", remote.id: "<run>"}
            assert step_triples(ctl.runs.events(remote.id), replace) == \
                step_triples(ctl.runs.events(local.id), replace), f"example {n}"

        assert docker_calls == []                                    # every image run used the warm container
        print(f"\nimage run wall times (s): {', '.join(f'{w:.2f}' for w in walls)}")
        assert walls[1] < 60
    finally:
        serving.serve.stop(image)
        remove_image(image)


def test_mcp_registry_entry_reaches_the_container_only_through_registry_json(tmp_path, shared_venvs, online):
    from wynd.runtime.mcp.fake_server import serve_http

    script = {"responses": [{"match": {"input": {"number": 7}}, "output": {"exit": "done", "title": "Fix login"}}]}
    with serve_http() as server:
        port = server.httpd.server_address[1]
        ws = mcp_workspace(tmp_path, shared_venvs, "notes")
        env = {"WYND_FAKE_PROVIDER_SCRIPT": json.dumps(script),
               "WYND_MCP_NOTES_URL": f"http://host.docker.internal:{port}/mcp"}
        ctl = open_controller(ws, env)
        ctl.ctx.stores.registry.put("mcp", "notes", {"name": "notes", "transport": "http", "url": server.url})
        image = build_image(ctl, MCP_PID)
        try:
            withheld = subprocess.run(["docker", "run", "--rm", image, "env-check"], capture_output=True, text=True)
            assert withheld.returncode == 1
            assert "WYND_REGISTRY_JSON" in withheld.stderr

            ctl.serve.serve(image, timeout=READY_TIMEOUT)
            assert server.mcp.messages == []
            run = ctl.runs.run(MCP_PID, {"number": 7}, target=ImageTarget())

            assert (run.exit, run.outputs) == ("done", {"title": "Fix login"}), run.error
            methods = [message.get("method") for message in server.mcp.messages]
            assert "initialize" in methods and "tools/list" in methods   # connected and verified the snapshot
        finally:
            ctl.serve.stop(image)
            remove_image(image)

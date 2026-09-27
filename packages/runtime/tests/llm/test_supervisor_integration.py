"""Supervisor integration (PLAN §3.17; `$DRAFTS/03 §16` test_supervisor_integration): the real `Executor` +
`WorkerPool` (venv python = `sys.executable`) on a two-step deterministic plan behind the run API, driven by
`RunApiClient.run`, with a trace equal to the local executor's except for `mode`; the `wynd-supervisor` console
commands (`serve` as a subprocess with drain on SIGTERM and the env-check gate, `env-check`, `check-plan`)."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from wynd.runtime.executor import Executor
from wynd.runtime.storage.local import FileRunRegistry
from wynd.runtime.supervisor.client import RunApiClient, RunApiError
from wynd.runtime.supervisor.http import make_server
from wynd.runtime.supervisor.main import main
from wynd.runtime.supervisor.runs import RunManager
from wynd.runtime.worker.pool import WorkerPool
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.fragments import EnvVar
from wynd.spec.lockfiles import BaseChoice, ProcessLock, StepLock, dump_lock
from wynd.spec.plan import PlanNode, PlanProcess, PlanStep, PlanVenv, RunPlan
from wynd.spec.process_doc import ProcessDoc
from wynd.spec.yamlio import parse_model

PROCESS = """
kind: process
name: shout
goal: Shout the text and count its letters.
entry: upper
inputs: {text: string, delay: "number?"}
outputs: {text: string, letters: integer}
steps:
  upper: {use: ./steps/upper}
  count: {use: ./steps/count}
edges:
  - from: upper.done
    to: count
    with: {text: steps.upper.outputs.text}
  - from: count.done
    to: $exit.done
    with: {text: steps.count.outputs.text, letters: steps.count.outputs.letters}
"""
UPPER = '''
import time
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep


class Upper(DeterministicStep):
    """Upper-case the text (after an optional delay)."""

    class Input(BaseModel):
        text: str
        delay: float | None = None

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        text: str

    def run(self, input):
        if input.delay:
            time.sleep(input.delay)
        return self.Output(text=input.text.upper())
'''
COUNT = '''
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep


class Count(DeterministicStep):
    """Count the letters."""

    class Input(BaseModel):
        text: str

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        text: str
        letters: int

    def run(self, input):
        return self.Output(text=input.text, letters=sum(c.isalpha() for c in input.text))
'''
BROKEN = '''
import not_a_real_module_anywhere  # noqa: F401
'''
VOLATILE = frozenset({"ts", "run_id", "workspace", "timings", "duration_ms", "pid", "startup_ms", "mode"})


def write_plan(root: Path, *, mode: str = "image", broken: bool = False) -> RunPlan:
    steps = {}
    for name, cls, source, venv in (("upper", "Upper", UPPER, "deps_1"), ("count", "Count", COUNT, "deps_2")):
        package = root / "steps" / name
        package.mkdir(parents=True, exist_ok=True)
        (package / f"{name}.py").write_text(textwrap.dedent(BROKEN if broken and name == "count" else source))
        sid = f"shout#{name}"
        steps[sid] = PlanStep(id=sid, kind="deterministic", entrypoint=f"{name}:{cls}", venv=venv,
                              package_dir=str(package),
                              lock=StepLock(name=name, kind="deterministic", entrypoint=f"{name}:{cls}"))
    doc = parse_model(textwrap.dedent(PROCESS), ProcessDoc)
    process = PlanProcess(id="shout", dir=None, definition=doc,
                          nodes={key: PlanNode(step=f"shout#{key}") for key in doc.steps})
    return RunPlan(
        mode=mode, root="shout", commit="c" * 40, provider="fake", venv_root=str(root / "venvs"),
        venvs=[PlanVenv(id="deps_1", python=sys.executable, steps=["shout#upper"]),
               PlanVenv(id="deps_2", python=sys.executable, steps=["shout#count"])],
        steps=steps, processes={"shout": process},
    )


def write_lock(root: Path, plan: RunPlan) -> Path:
    lock = ProcessLock(
        process="shout", commit="c" * 40, source_sha="c" * 40, process_hash="sha256:" + "0" * 64,
        runtime_version="0.1.0", platform="linux/arm64",
        base=BaseChoice(requested="debian-slim-python", variant="slim", version="0.1.0", image="wynd-base:0.1.0-slim"),
        plan=plan,
    )
    path = root / "process.lock.yaml"
    path.write_text(dump_lock(lock))
    return path


def write_manifest(root: Path, *, needs: str | None = None) -> Path:
    vars = [EnvVar(name=needs, description="needed by the test", used_by=["step:shout#upper"])] if needs else []
    path = root / "process.env.yaml"
    path.write_text(dump_lock(EnvManifest(process="shout", vars=vars)))
    return path


def stores(root: Path) -> Any:
    from wynd.runtime.storage import stores_from_env

    return stores_from_env({"WYND_HOME": str(root / "home")}, data_dir=root / "data")


def normalised(events: list[dict]) -> list[dict]:
    def strip(value: Any) -> Any:
        match value:
            case dict():
                return {k: strip(v) for k, v in value.items() if k not in VOLATILE}
            case list():
                return [strip(v) for v in value]
            case _:
                return value

    return [strip(event) for event in events]


# --- in-process: real executor and workers behind the HTTP layer --------------------------------------------------------


def test_a_run_through_the_api_matches_the_local_executor_except_mode(tmp_path):
    image_plan = write_plan(tmp_path)
    image_stores = stores(tmp_path / "image")
    with WorkerPool(image_plan) as pool:
        manager = RunManager(Executor(image_plan, pool, image_stores), image_stores,
                             uploads_dir=tmp_path / "image" / "data" / "uploads", workers=pool.status)
        server = make_server(manager, host="127.0.0.1", port=0)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        try:
            pool.start()
            manager.mark_ready()
            client = RunApiClient(f"http://127.0.0.1:{server.server_address[1]}", timeout_s=30)
            ready = client.ready()
            assert ready["status"] == "ready" and [w["venv"] for w in ready["workers"]] == ["deps_1", "deps_2"]
            streamed: list[dict] = []
            run = client.run({"text": "hi there"}, run_id="api-run", metadata={"trigger": "api"},
                             on_event=streamed.append)
            second = client.run({"text": "again"})
            after = client.ready()["workers"]
        finally:
            manager.drain(10)
            server.shutdown()
            server.server_close()

    assert (run.status, run.exit, run.outputs) == ("succeeded", "done", {"text": "HI THERE", "letters": 7})
    assert (run.mode, run.commit, run.process) == ("image", "c" * 40, "shout")
    assert second.outputs == {"text": "AGAIN", "letters": 5}
    assert [w["pid"] for w in after] == [w["pid"] for w in ready["workers"]]   # the same warm workers served both
    image_trace = image_stores.traces.read("api-run")
    assert streamed == image_trace                             # the SSE stream carries exactly the trace
    assert image_stores.runs.get("api-run")["meta"] == {"trigger": "api"}

    local_plan = image_plan.model_copy(update={"mode": "local"})
    local_stores = stores(tmp_path / "local")
    with WorkerPool(local_plan) as pool:
        local = Executor(local_plan, pool, local_stores).run({"text": "hi there"}, run_id="local-run",
                                                              metadata={"trigger": "api"})
    local_trace = local_stores.traces.read(local.run_id)
    assert local.outputs == run.outputs
    assert (image_trace[0]["mode"], local_trace[0]["mode"]) == ("image", "local")
    assert normalised(image_trace) == normalised(local_trace)
    assert [e["type"] for e in image_trace] == ["run.start", "step.start", "step.end", "edge.taken", "step.start",
                                                "step.end", "edge.taken", "run.end"]


# --- the console script ---------------------------------------------------------------------------------------------------


def serve(tmp_path: Path, **extra_env: str) -> tuple[subprocess.Popen, RunApiClient, list[str]]:
    lock = write_lock(tmp_path, write_plan(tmp_path))
    env = {**os.environ, "WYND_DATA_DIR": str(tmp_path / "data"), "WYND_HOST": "127.0.0.1", "WYND_PORT": "0",
           "WYND_DRAIN_TIMEOUT_S": "30", **extra_env}
    proc = subprocess.Popen([sys.executable, "-m", "wynd.runtime.supervisor", "serve", "--plan", str(lock)],
                            env=env, stderr=subprocess.PIPE, text=True)
    lines: list[str] = []
    listening = threading.Event()

    def pump() -> None:
        for line in proc.stderr:
            lines.append(line)
            if "listening on" in line:
                listening.set()

    threading.Thread(target=pump, daemon=True).start()
    assert listening.wait(30), "".join(lines)
    [url] = re.findall(r"listening on (http://\S+)", "".join(lines))
    return proc, RunApiClient(url, token=extra_env.get("WYND_RUN_API_TOKEN"), timeout_s=30), lines


def stop(proc: subprocess.Popen) -> int:
    proc.send_signal(signal.SIGTERM)
    return proc.wait(60)


def test_serve_runs_until_sigterm_and_drains_the_run_in_flight(tmp_path):
    manifest = write_manifest(tmp_path)
    proc, client, lines = serve(tmp_path, WYND_ENV_MANIFEST=str(manifest), WYND_RUN_API_TOKEN="tok")
    try:
        client.wait_ready(timeout=60)
        assert client.info().name == "shout"
        created = client.submit({"text": "slow", "delay": 1.5}, run_id="slow-run")
        deadline = time.monotonic() + 30
        while client.get(created.run_id).status != "running":
            assert time.monotonic() < deadline
            time.sleep(0.05)
        proc.send_signal(signal.SIGTERM)
        deadline = time.monotonic() + 10
        while True:
            try:
                client.ready()
            except RunApiError as err:
                assert (err.status, err.code) == (503, "draining")
                break
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert proc.wait(60) == 0, "".join(lines)
    finally:
        if proc.poll() is None:
            proc.kill()
    record = FileRunRegistry(tmp_path / "data" / "registry").get("slow-run")
    assert (record["status"], record["outputs"]) == ("succeeded", {"text": "SLOW", "letters": 4})
    assert record["mode"] == "image"


def test_serve_refuses_to_become_ready_when_the_env_check_fails(tmp_path):
    manifest = write_manifest(tmp_path, needs="WYND_TEST_NEEDED_TOKEN")
    proc, client, lines = serve(tmp_path, WYND_ENV_MANIFEST=str(manifest))
    try:
        deadline = time.monotonic() + 30
        while not client.health():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        time.sleep(0.5)                                          # workers are warm by now; readiness must not come
        with pytest.raises(RunApiError) as err:
            client.ready()
        assert (err.value.status, err.value.code) == (503, "starting")
        [problem] = err.value.body["problems"]
        assert "WYND_TEST_NEEDED_TOKEN" in problem and "E-ENV-MISSING" in problem
        with pytest.raises(RunApiError) as err:
            client.submit({"text": "x"})
        assert (err.value.status, err.value.code) == (503, "not_ready")
        assert stop(proc) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    assert any("env check failed" in line for line in lines)


def test_env_check_command_exit_codes(tmp_path, monkeypatch, capsys):
    manifest = write_manifest(tmp_path, needs="WYND_TEST_NEEDED_TOKEN")
    monkeypatch.delenv("WYND_TEST_NEEDED_TOKEN", raising=False)
    assert main(["env-check", "--manifest", str(manifest)]) == 1
    err = capsys.readouterr().err
    assert "E-ENV-MISSING" in err and "WYND_TEST_NEEDED_TOKEN" in err and "step:shout#upper" in err

    monkeypatch.setenv("WYND_TEST_NEEDED_TOKEN", "value-not-printed")
    monkeypatch.setenv("WYND_ENV_MANIFEST", str(manifest))
    assert main(["env-check"]) == 0
    assert "value-not-printed" not in capsys.readouterr().err

    assert main(["env-check", "--manifest", str(tmp_path / "missing.yaml")]) == 1
    assert "cannot read env manifest" in capsys.readouterr().err


def test_check_plan_starts_every_worker_and_fails_on_an_import_error(tmp_path, capsys):
    good = write_lock(tmp_path / "good", write_plan(tmp_path / "good"))
    assert main(["check-plan", str(good)]) == 0
    assert capsys.readouterr().out.strip() == "ok: 2 steps in 2 venvs"

    bad = write_lock(tmp_path / "bad", write_plan(tmp_path / "bad", broken=True))
    assert main(["check-plan", str(bad)]) == 1
    err = capsys.readouterr().err
    assert "FAIL shout#count" in err and "not_a_real_module_anywhere" in err
    assert "1 of 2 steps failed to load" in err

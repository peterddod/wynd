"""`WorkerPool` and `InProcessDispatcher` (PLAN §5.3; `$DRAFTS/02 §5.5, §6.2`, §13 test_pool): one real
`sys.executable` worker per venv, crash and timeout respawn, concurrency, the in-process seam's parity."""

from __future__ import annotations

import logging
import os
import re
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from wynd.runtime.errors import StepDefinitionError
from wynd.runtime.policy import ExecPolicy
from wynd.runtime.worker.client import StepTimeout, WorkerCrashed, WorkerRpcError
from wynd.runtime.worker.loader import load_step_class
from wynd.runtime.worker.pool import InProcessDispatcher, WorkerPool
from wynd.runtime.worker.protocol import UNKNOWN_STEP, RunStepParams
from wynd.spec.lockfiles import RetryPolicy, StepLock
from wynd.spec.plan import PlanStep, PlanVenv, RunPlan

KIT = '''
import os
import sys
import time
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep


class Text(BaseModel):
    text: str = ""


class Done(BaseModel):
    exit: Literal["done"] = "done"
    value: str = ""


class Empty(BaseModel):
    exit: Literal["empty"] = "empty"


class Pid(DeterministicStep):
    """Report the worker's pid and how often this worker ran the step."""

    Input = Text
    Output = Done | Empty

    def run(self, input):
        count = self.runtime.cache.get("count", 0) + 1
        self.runtime.cache.set("count", count)
        self.runtime.logger.info("counted %d", count)
        return Done(value=f"{os.getpid()}:{count}")


class Boom(DeterministicStep):
    Input = Text
    Output = Done

    def run(self, input):
        sys.stderr.write("going down\\n")
        os._exit(3)


class Sleepy(DeterministicStep):
    Input = Text
    Output = Done

    def run(self, input):
        started = time.time()
        time.sleep(float(input.text))
        return Done(value=f"{started}:{time.time()}:{os.getpid()}")


class Fails(DeterministicStep):
    Input = Text
    Output = Done

    def run(self, input):
        raise ValueError("bad " + input.text)
'''

SLOW_IMPORT = '''
import time
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep

time.sleep(2.0)


class Slow(DeterministicStep):
    class Input(BaseModel):
        pass

    class Done(BaseModel):
        exit: Literal["done"] = "done"

    Output = Done

    def run(self, input):
        return self.Done()
'''

BROKEN = "import wynd_missing_dependency\n"


def write_module(directory: Path, name: str, source: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(textwrap.dedent(source))
    return directory


def plan_step(step_id: str, entrypoint: str, venv: str, package_dir: Path) -> PlanStep:
    lock = StepLock(name=package_dir.name, kind="deterministic", entrypoint=entrypoint)
    return PlanStep(id=step_id, kind="deterministic", entrypoint=entrypoint, venv=venv, package_dir=str(package_dir),
                    lock=lock)


def make_plan(tmp_path: Path, venvs: dict[str, list[PlanStep]], *, python: str | None = sys.executable,
              extra: list[PlanStep] = ()) -> RunPlan:
    steps = {step.id: step for entries in venvs.values() for step in entries}
    steps.update({step.id: step for step in extra})
    return RunPlan(
        mode="local", root="p", provider="fake", venv_root=str(tmp_path / "venvs"),
        venvs=[PlanVenv(id=venv_id, python=python, steps=[step.id for step in entries])
               for venv_id, entries in venvs.items()],
        steps=steps, processes={},
    )


def params(step_id: str, workspace: Path, text: str = "") -> RunStepParams:
    return RunStepParams(
        run_id="run-test", step_path=step_id.rpartition("#")[2], step_run=1, step_id=step_id, inputs={"text": text},
        workspace=str(workspace), policy=ExecPolicy(kind="deterministic", retries=RetryPolicy()),
    )


def ignore(event: dict) -> None:
    return None


def pid_count(pool: WorkerPool, workspace: Path, events: list | None = None) -> tuple[int, int]:
    on_event = ignore if events is None else events.append
    result = pool.dispatch("p#pid", params("p#pid", workspace), on_event=on_event, timeout=60)
    pid, count = result.outputs["value"].split(":")
    return int(pid), int(count)


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.fixture
def kit(tmp_path) -> Path:
    return write_module(tmp_path / "kit", "kit", KIT)


@pytest.fixture
def pool(tmp_path, kit):
    steps = [plan_step(f"p#{name}", f"kit:{name.title()}", "v1", kit) for name in ("pid", "boom", "sleepy", "fails")]
    with WorkerPool(make_plan(tmp_path, {"v1": steps}), cwd=str(tmp_path)) as pool:
        yield pool


def test_a_crash_raises_worker_crashed_and_the_next_dispatch_respawns(pool, tmp_path):
    events: list[dict] = []
    first, count = pid_count(pool, tmp_path, events)
    starts = [event for event in events if event["type"] == "worker.start"]
    assert starts == [{"type": "worker.start", "venv": "v1", "pid": first, "startup_ms": starts[0]["startup_ms"]}]
    assert starts[0]["startup_ms"] > 0 and count == 1

    with pytest.raises(WorkerCrashed) as crashed:
        pool.dispatch("p#boom", params("p#boom", tmp_path), on_event=ignore, timeout=60)
    assert crashed.value.returncode == 3
    assert "exited with code 3 during run_step" in str(crashed.value)
    assert pool.status() == [{"venv": "v1", "pid": None, "alive": False}]

    events.clear()
    second, count = pid_count(pool, tmp_path, events)
    assert second != first and gone(first)
    assert count == 1                                    # the cache lives and dies with its worker
    assert [event["pid"] for event in events if event["type"] == "worker.start"] == [second]


def test_a_timeout_kills_the_worker_and_the_next_dispatch_restarts_it(pool, tmp_path):
    first, _ = pid_count(pool, tmp_path)
    started = time.monotonic()
    with pytest.raises(StepTimeout):
        pool.dispatch("p#sleepy", params("p#sleepy", tmp_path, "5"), on_event=ignore, timeout=0.5)
    assert time.monotonic() - started < 3
    assert gone(first)
    assert pool.status()[0]["alive"] is False
    second, count = pid_count(pool, tmp_path)
    assert second != first and count == 1


def test_warm_workers_are_reused_without_worker_start(pool, tmp_path):
    pool.start()
    ((status),) = pool.status()
    assert status["alive"] is True
    events: list[dict] = []
    assert pid_count(pool, tmp_path, events) == (status["pid"], 1)
    assert pid_count(pool, tmp_path, events) == (status["pid"], 2)
    assert [event["type"] for event in events] == ["step.log", "step.log"]
    assert events[0]["message"] == "counted 1"


def test_start_spawns_every_venv_concurrently_and_is_idempotent(tmp_path):
    slow = write_module(tmp_path / "slow", "slow", SLOW_IMPORT)
    venvs = {venv_id: [plan_step(f"{venv_id}#slow", "slow:Slow", venv_id, slow)] for venv_id in ("v1", "v2")}
    with WorkerPool(make_plan(tmp_path, venvs)) as pool:
        assert pool.status() == [{"venv": venv_id, "pid": None, "alive": False} for venv_id in ("v1", "v2")]
        started = time.monotonic()
        pool.start()
        elapsed = time.monotonic() - started
        assert elapsed < 3.9                              # both imports sleep 2 s: sequential would take >= 4 s
        status = pool.status()
        assert [entry["alive"] for entry in status] == [True, True]
        assert status[0]["pid"] != status[1]["pid"]
        pool.start()
        assert pool.status() == status


def test_the_venv_lock_serialises_concurrent_dispatches(pool, tmp_path):
    pool.start()
    results: list[str] = []

    def dispatch() -> None:
        result = pool.dispatch("p#sleepy", params("p#sleepy", tmp_path, "0.3"), on_event=ignore, timeout=30)
        results.append(result.outputs["value"])

    threads = [threading.Thread(target=dispatch) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    spans = sorted(tuple(float(part) for part in value.split(":")[:2]) for value in results)
    assert len(spans) == 3
    assert all(earlier[1] <= later[0] for earlier, later in zip(spans, spans[1:]))
    assert {value.split(":")[2] for value in results} == {str(pool.status()[0]["pid"])}


def test_a_crash_during_init_recovers_once_the_environment_is_fixed(tmp_path, kit, caplog):
    python = tmp_path / "bin" / "python"
    python.parent.mkdir()
    python.write_text("#!/bin/sh\nexit 7\n")
    python.chmod(0o755)
    plan = make_plan(tmp_path, {"v1": [plan_step("p#pid", "kit:Pid", "v1", kit)]}, python=str(python))
    with WorkerPool(plan) as pool:
        with caplog.at_level(logging.WARNING, logger="wynd.runtime.worker"):
            pool.start()                                  # logged, not raised: the next dispatch retries
        assert "failed to start" in caplog.text
        with pytest.raises(WorkerCrashed) as crashed:
            pool.dispatch("p#pid", params("p#pid", tmp_path), on_event=ignore, timeout=30)
        assert crashed.value.returncode == 7 and "during init" in str(crashed.value)

        python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        events: list[dict] = []
        pid, count = pid_count(pool, tmp_path, events)
        assert count == 1 and events[0] == {**events[0], "type": "worker.start", "pid": pid}


def test_a_missing_interpreter_is_a_worker_crash(tmp_path, kit):
    plan = make_plan(tmp_path, {"v1": [plan_step("p#pid", "kit:Pid", "v1", kit)]}, python=None)
    with WorkerPool(plan) as pool:
        with pytest.raises(WorkerCrashed, match=re.escape(f"cannot start worker {tmp_path}/venvs/v1/bin/python")):
            pool.dispatch("p#pid", params("p#pid", tmp_path), on_event=ignore, timeout=30)


def test_rpc_errors_propagate_and_keep_the_worker(tmp_path, kit):
    orphan = plan_step("p#orphan", "kit:Pid", "v1", kit)            # assigned to v1 but not served by it
    plan = make_plan(tmp_path, {"v1": [plan_step("p#pid", "kit:Pid", "v1", kit)]}, extra=[orphan])
    with WorkerPool(plan) as pool:
        pid, _ = pid_count(pool, tmp_path)
        with pytest.raises(WorkerRpcError) as error:
            pool.dispatch("p#orphan", params("p#orphan", tmp_path), on_event=ignore, timeout=30)
        assert error.value.code == UNKNOWN_STEP
        assert pid_count(pool, tmp_path) == (pid, 2)
        with pytest.raises(KeyError):
            pool.dispatch("p#unplanned", params("p#unplanned", tmp_path), on_event=ignore, timeout=30)


def test_describe_is_cached_per_venv_and_refuses_broken_steps(tmp_path, kit):
    broken = write_module(tmp_path / "broken", "broken", BROKEN)
    steps = [plan_step("p#pid", "kit:Pid", "v1", kit), plan_step("p#fails", "kit:Fails", "v1", kit),
             plan_step("p#broken", "broken:Broken", "v1", broken)]
    with WorkerPool(make_plan(tmp_path, {"v1": steps})) as pool:
        description = pool.describe("p#pid")
        assert (description.id, description.kind, description.doc) == (
            "p#pid", "deterministic", "Report the worker's pid and how often this worker ran the step.",
        )
        assert description.cls.endswith(".kit:Pid") and list(description.exits) == ["done", "empty", "error"]
        pid = pool.status()[0]["pid"]
        os.kill(pid, 9)
        assert pool.describe("p#fails").cls.endswith(".kit:Fails")    # served from the cache: no respawn
        deadline = time.monotonic() + 10
        while pool.status()[0]["alive"] and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pool.status() == [{"venv": "v1", "pid": pid, "alive": False}]
        with pytest.raises(StepDefinitionError, match="step p#broken failed to import: ModuleNotFoundError"):
            pool.describe("p#broken")
        with pytest.raises(KeyError):
            pool.describe("p#unplanned")


def test_call_reaches_any_method_of_a_venv_worker(pool):
    reply = pool.call("v1", "ping", {}, on_event=ignore, timeout=30)
    assert reply == {"pid": pool.status()[0]["pid"]}


def test_close_shuts_every_worker_down(tmp_path, kit):
    venvs = {venv_id: [plan_step(f"{venv_id}#pid", "kit:Pid", venv_id, kit)] for venv_id in ("v1", "v2")}
    pool = WorkerPool(make_plan(tmp_path, venvs))
    pool.start()
    pids = [entry["pid"] for entry in pool.status()]
    pool.close()
    assert all(gone(pid) for pid in pids)
    assert [entry["alive"] for entry in pool.status()] == [False, False]


@pytest.fixture
def steps_namespace():
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name == "wynd_steps" or name.startswith("wynd_steps."):
            del sys.modules[name]


def test_in_process_dispatcher_matches_the_pool(tmp_path, kit, steps_namespace):
    steps = [plan_step("p#pid", "kit:Pid", "v1", kit), plan_step("p#fails", "kit:Fails", "v1", kit)]
    classes = {step.id: load_step_class(step.id, step.entrypoint, step.package_dir) for step in steps}
    local = InProcessDispatcher(classes)
    local.start()
    with WorkerPool(make_plan(tmp_path, {"v1": steps})) as pool:
        pool.start()
        for step_id in classes:
            assert local.describe(step_id) == pool.describe(step_id)
            for text in ("a", "b"):
                pool_events: list[dict] = []
                local_events: list[dict] = []
                remote = pool.dispatch(step_id, params(step_id, tmp_path, text), on_event=pool_events.append,
                                       timeout=30)
                here = local.dispatch(step_id, params(step_id, tmp_path, text), on_event=local_events.append,
                                      timeout=0.001)             # ignored in-process
                if step_id == "p#pid":                           # the step reports the pid of whoever ran it
                    remote.outputs["value"] = remote.outputs["value"].split(":")[1]
                    here.outputs["value"] = here.outputs["value"].split(":")[1]
                    remote.summary.key_outputs["value"] = here.summary.key_outputs["value"] = "<pid:count>"
                assert remote.model_dump(exclude={"timings"}) == here.model_dump(exclude={"timings"})
                assert pool_events == local_events
    local.close()
    with pytest.raises(KeyError):
        local.describe("p#unknown")

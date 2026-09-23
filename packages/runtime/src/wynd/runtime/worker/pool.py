"""Dispatchers: the subprocess `WorkerPool` (one worker per venv) and the in-process test seam (PLAN §5.3;
`$DRAFTS/02 §6.2`).

A worker runs one request at a time (env and cwd are process-global), so each venv has a lock and concurrent runs
interleave at step granularity. A crashed or timed-out worker is dropped; the next call to its venv spawns and
inits a fresh one (announced by a `worker.start` event), so a fixed environment recovers without a restart.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any, Protocol

from wynd.runtime.errors import StepDefinitionError
from wynd.runtime.handle import StepCache
from wynd.runtime.interface import StepDescription, describe_step
from wynd.runtime.middleware import run_chain
from wynd.runtime.worker.client import StepTimeout, WorkerClient, WorkerCrashed, WorkerRpcError
from wynd.runtime.worker.protocol import RunStepParams, RunStepResult, init_step

if TYPE_CHECKING:
    from wynd.runtime.step import Step
    from wynd.spec.plan import PlanVenv, RunPlan

log = logging.getLogger("wynd.runtime.worker")


class Dispatcher(Protocol):
    """What the executor runs steps through."""

    def start(self) -> None: ...

    def describe(self, step_id: str) -> StepDescription: ...

    def dispatch(
        self,
        step_id: str,
        params: RunStepParams,
        *,
        on_event: Callable[[dict[str, Any]], None],
        timeout: float | None,
    ) -> RunStepResult: ...                  # raises StepTimeout, WorkerCrashed, WorkerRpcError

    def close(self) -> None: ...


class WorkerPool:
    """At most one worker per `PlanVenv`, guarded by a per-venv lock; lazy respawn after a crash or timeout."""

    def __init__(self, plan: RunPlan, *, env: Mapping[str, str] | None = None, cwd: str | None = None) -> None:
        self.plan = plan
        self.env = dict(os.environ if env is None else env)
        self.cwd = cwd
        self._venvs = {venv.id: venv for venv in plan.venvs}
        self._locks = {venv_id: threading.Lock() for venv_id in self._venvs}
        self._clients: dict[str, WorkerClient] = {}
        self._descriptions: dict[str, dict[str, Any]] = {}     # step id -> StepDescription JSON | {"error": ...}

    def start(self, venvs: Iterable[str] | None = None) -> None:
        """Warm up: spawn every requested venv's worker at once, then collect the inits. Idempotent; a venv that is
        busy or already warm is skipped, and one that fails to start is logged and retried on its next call."""
        requested = list(self._venvs) if venvs is None else list(venvs)
        held = [venv_id for venv_id in requested if self._locks[venv_id].acquire(blocking=False)]
        try:
            spawned = []
            for venv_id in held:
                if self._live(venv_id) is not None:
                    continue
                client = self._new_client(self._venvs[venv_id])
                try:
                    client.spawn()
                except WorkerCrashed as err:
                    log.warning("worker for venv %s failed to start: %s", venv_id, err)
                    continue
                spawned.append((venv_id, client))
            for venv_id, client in spawned:
                try:
                    client.start()
                except (WorkerCrashed, WorkerRpcError) as err:
                    client.kill()
                    log.warning("worker for venv %s failed to start: %s", venv_id, err)
                    continue
                self._clients[venv_id] = client
        finally:
            for venv_id in held:
                self._locks[venv_id].release()

    def dispatch(
        self,
        step_id: str,
        params: RunStepParams,
        *,
        on_event: Callable[[dict[str, Any]], None],
        timeout: float | None,
    ) -> RunStepResult:
        """Run one step in its venv's worker. A step `error` exit is a result; raises `StepTimeout` (worker killed),
        `WorkerCrashed` (worker dropped), `WorkerRpcError`; KeyError for a step id not in the plan."""
        venv_id = self.plan.steps[step_id].venv
        result = self.call(venv_id, "run_step", params.model_dump(mode="json"), on_event=on_event, timeout=timeout)
        return RunStepResult.model_validate(result)

    def describe(self, step_id: str) -> StepDescription:
        """The worker's description of one step; the first call per venv describes all its steps and caches them.
        StepDefinitionError when the step failed to import (or drifted from its lock)."""
        if step_id not in self._descriptions:
            venv_id = self.plan.steps[step_id].venv
            result = self.call(venv_id, "describe", {"ids": None}, on_event=_ignore, timeout=None)
            self._descriptions.update(result["steps"])
        entry = self._descriptions[step_id]
        error = entry.get("error")
        if error is not None:
            raise StepDefinitionError(f"step {step_id} failed to import: {error['type']}: {error['message']}")
        return StepDescription.model_validate(entry)

    def status(self) -> list[dict[str, Any]]:
        """`[{"venv", "pid", "alive"}]` for `/readyz`, one entry per plan venv."""
        status = []
        for venv_id in self._venvs:
            client = self._clients.get(venv_id)
            alive = client is not None and client.alive
            status.append({"venv": venv_id, "pid": client.pid if client else None, "alive": alive})
        return status

    def call(
        self,
        venv_id: str,
        method: str,
        params: dict[str, Any],
        *,
        on_event: Callable[[dict[str, Any]], None],
        timeout: float | None,
    ) -> dict[str, Any]:
        """Any request to `venv_id`'s worker (spawned on demand). `timeout` bounds the whole call, including waiting
        for the venv and a respawn; on expiry the worker is killed."""
        deadline = None if timeout is None else time.monotonic() + timeout
        lock = self._locks[venv_id]
        if not lock.acquire(timeout=-1 if deadline is None else _remaining(deadline)):
            raise StepTimeout(f"venv {venv_id} stayed busy for the whole {timeout:g}s timeout")
        try:
            client = self._ensure(venv_id, on_event, deadline)
            try:
                return client.call(method, params, on_event=on_event, timeout=_remaining(deadline))
            except (StepTimeout, WorkerCrashed):
                self._drop(venv_id)
                raise
        finally:
            lock.release()

    def close(self) -> None:
        """Shut every worker down (waits for in-flight calls)."""
        for venv_id in list(self._clients):
            with self._locks[venv_id]:
                client = self._clients.pop(venv_id, None)
                if client is not None:
                    client.close()

    def __enter__(self) -> WorkerPool:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _ensure(
        self, venv_id: str, on_event: Callable[[dict[str, Any]], None], deadline: float | None
    ) -> WorkerClient:
        """The live worker of `venv_id`, spawning and initialising one if needed (caller holds the venv lock)."""
        client = self._live(venv_id)
        if client is not None:
            return client
        client = self._new_client(self._venvs[venv_id])
        started = time.perf_counter()
        try:
            client.start(timeout=_remaining(deadline))
        except (StepTimeout, WorkerCrashed, WorkerRpcError):
            client.kill()
            raise
        self._clients[venv_id] = client
        startup_ms = (time.perf_counter() - started) * 1000
        on_event({"type": "worker.start", "venv": venv_id, "pid": client.pid, "startup_ms": startup_ms})
        return client

    def _live(self, venv_id: str) -> WorkerClient | None:
        client = self._clients.get(venv_id)
        if client is not None and not client.alive:
            self._drop(venv_id)
            return None
        return client

    def _drop(self, venv_id: str) -> None:
        client = self._clients.pop(venv_id, None)
        if client is not None:
            client.kill()

    def _new_client(self, venv: PlanVenv) -> WorkerClient:
        python = venv.python or f"{self.plan.venv_root}/{venv.id}/bin/python"
        steps = [init_step(self.plan.steps[step_id]) for step_id in venv.steps]
        return WorkerClient(python, steps, env=self.env, cwd=self.cwd)


class InProcessDispatcher:
    """Runs `run_chain` in-process for `{step_id: Step subclass}` (unit-test seam; ignores timeouts)."""

    def __init__(self, classes: Mapping[str, type[Step]]) -> None:
        self.classes = dict(classes)
        self._caches = {step_id: StepCache() for step_id in self.classes}

    def start(self) -> None:
        return None

    def describe(self, step_id: str) -> StepDescription:
        return describe_step(self.classes[step_id], step_id)

    def dispatch(
        self,
        step_id: str,
        params: RunStepParams,
        *,
        on_event: Callable[[dict[str, Any]], None],
        timeout: float | None,
    ) -> RunStepResult:
        return run_chain(self.classes[step_id], params, emit=on_event, cache=self._caches[step_id]).to_result()

    def close(self) -> None:
        return None


def _remaining(deadline: float | None) -> float | None:
    return None if deadline is None else max(0.0, deadline - time.monotonic())


def _ignore(event: dict[str, Any]) -> None:
    return None

"""`RunManager`: the run queue, per-run state and event buffers, retention and drain (PLAN §3.17;
`$DRAFTS/03 §13.2`).

A run is accepted synchronously (idempotency, capacity, uploads, input validation), queued on a thread pool of
`max_concurrent` workers and executed with `executor.run(..., cassette_mode="live", on_event=<fan-out>)`. Each run
keeps an event buffer that interleaves `status` and `trace` events and ends with one `end` event; SSE readers and
long-polls wait on the run's condition.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse
from urllib.request import url2pathname

from wynd.runtime import __version__
from wynd.runtime.errors import InvalidProcessInputs
from wynd.runtime.ids import new_id
from wynd.runtime.supervisor.files import materialise_files, release_files
from wynd.runtime.supervisor.schema import ProcessInfo, Run, RunCreated, RunRequest, RunSummary, links
from wynd.spec.base import RESERVED_EXIT
from wynd.spec.records import ProcessError

if TYPE_CHECKING:
    from wynd.runtime.storage import Stores
    from wynd.spec.plan import RunPlan

State = Literal["starting", "ready", "draining"]
TERMINAL = frozenset({"succeeded", "failed"})


class RunApiRefusal(Exception):
    """A request the run API answers with an error body: `status`, `code`, `message`, `details`."""

    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


@dataclass
class RunState:
    run_id: str
    fingerprint: str
    inputs: dict[str, Any]                         # validated, `$file` references substituted
    metadata: dict[str, Any]
    created_at: datetime
    uploads: Path
    status: str = "queued"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit: str | None = None
    outputs: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    duration_ms: float | None = None
    usage: dict[str, Any] | None = None
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)   # index = SSE id
    cond: threading.Condition = field(default_factory=threading.Condition)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL

    def push(self, event: str, data: dict[str, Any]) -> None:
        with self.cond:
            self.events.append((event, data))
            self.cond.notify_all()

    def wait_events(self, after: int, timeout: float) -> tuple[list[tuple[int, str, dict[str, Any]]], bool]:
        """Events with id > `after` (waiting up to `timeout` for one), and whether the buffer is complete."""
        with self.cond:
            if len(self.events) <= after + 1 and not self._ended():
                self.cond.wait(timeout)
            new = [(i, *self.events[i]) for i in range(after + 1, len(self.events))]
            return new, self._ended()

    def wait_terminal(self, timeout: float) -> None:
        with self.cond:
            self.cond.wait_for(lambda: self.terminal, timeout)

    def _ended(self) -> bool:
        return bool(self.events) and self.events[-1][0] == "end"


def process_info(plan: RunPlan, *, limits: Mapping[str, int]) -> ProcessInfo:
    doc = plan.processes[plan.root].definition
    iface = doc.interface()
    return ProcessInfo(process=plan.root, name=doc.name, goal=doc.goal, commit=plan.commit,
                       runtime_version=__version__, inputs_schema=iface.input, outputs_schema=dict(iface.outputs),
                       steps=list(plan.steps), limits=dict(limits))


class RunManager:
    def __init__(
        self,
        executor: Any,                             # Executor (plan, validate_inputs, run)
        stores: Stores,
        *,
        uploads_dir: Path,
        max_concurrent: int = 4,
        max_queued: int = 64,
        retention: int = 1000,
        max_body_mb: int = 32,
        workers: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.executor = executor
        self.stores = stores
        self.plan: RunPlan = executor.plan
        self.uploads_dir = Path(uploads_dir)
        self.max_queued = max_queued
        self.retention = retention
        self.max_body_mb = max_body_mb
        self.workers = workers or (lambda: [])
        self.info = process_info(self.plan, limits={"max_concurrent_runs": max_concurrent,
                                                    "max_queued_runs": max_queued, "max_body_mb": max_body_mb})
        self.state: State = "starting"
        self.problems: list[str] = []              # why the supervisor is not ready (env check)
        self._runs: OrderedDict[str, RunState] = OrderedDict()   # creation order
        self._finished: list[str] = []             # finish order, for retention
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._pool = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="wynd-run")

    # --- lifecycle ----------------------------------------------------------------------------------------------------

    def mark_ready(self) -> None:
        with self._lock:
            if self.state == "starting":
                self.state = "ready"

    def begin_drain(self) -> None:
        with self._lock:
            self.state = "draining"

    def drain(self, timeout: float) -> bool:
        """Stop accepting runs and wait up to `timeout` for accepted runs to finish; True when all finished."""
        self.begin_drain()
        with self._idle:
            done = self._idle.wait_for(lambda: not self._active(), timeout)
        self._pool.shutdown(wait=done)
        return done

    def readiness(self) -> tuple[int, dict[str, Any]]:
        match self.state:
            case "ready":
                workers = self.workers()
                status = "ready" if all(w["alive"] for w in workers) else "degraded"
                return 200, {"status": status, "workers": workers}
            case "starting" if self.problems:
                return 503, {"status": "starting", "problems": list(self.problems)}
            case state:
                return 503, {"status": state}

    # --- runs ---------------------------------------------------------------------------------------------------------

    def submit(self, request: RunRequest) -> tuple[bool, RunCreated]:
        """Accept a run; returns (created, RunCreated). Raises RunApiRefusal (409/422/429/503)."""
        run_id = request.run_id or new_id("run")
        fingerprint = _fingerprint(request)
        with self._lock:
            existing = self._runs.get(run_id)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise RunApiRefusal(409, "run_id_conflict",
                                        f"run {run_id} was already submitted with different inputs")
                return False, RunCreated(run_id=run_id, status=existing.status, links=links(run_id))
            if self.state != "ready":
                raise RunApiRefusal(503, "not_ready", f"the supervisor is {self.state}")
            if self.stores.runs.get(run_id) is not None:
                raise RunApiRefusal(409, "run_id_conflict", f"run {run_id} already exists")
            if sum(run.status == "queued" for run in self._runs.values()) >= self.max_queued:
                raise RunApiRefusal(429, "queue_full", f"{self.max_queued} runs are already queued")
            uploads = self.uploads_dir / run_id
            try:
                inputs = materialise_files(request.inputs, request.files, uploads)
                inputs = self.executor.validate_inputs(inputs)
            except InvalidProcessInputs as err:
                release_files(uploads, None)
                raise RunApiRefusal(422, "invalid_inputs", str(err), err.errors) from None
            except ValueError as err:
                release_files(uploads, None)
                raise RunApiRefusal(422, "invalid_inputs", str(err)) from None
            run = RunState(run_id=run_id, fingerprint=fingerprint, inputs=inputs, metadata=dict(request.metadata),
                           created_at=_now(), uploads=uploads)
            self._runs[run_id] = run
            run.push("status", {"status": "queued"})
            self._pool.submit(self._execute, run)
        return True, RunCreated(run_id=run_id, status="queued", links=links(run_id))

    def get(self, run_id: str) -> RunState | None:
        with self._lock:
            return self._runs.get(run_id)

    def list(self, *, status: str | None = None, limit: int = 50) -> list[RunSummary]:
        with self._lock:
            runs = [run for run in reversed(self._runs.values()) if status is None or run.status == status]
        return [RunSummary(run_id=r.run_id, status=r.status, exit=r.exit, created_at=r.created_at,
                           finished_at=r.finished_at) for r in runs[:limit]]

    def view(self, run: RunState) -> Run:
        plan = self.plan
        return Run(run_id=run.run_id, process=plan.root, commit=plan.commit, runtime_version=__version__,
                   mode=plan.mode, status=run.status, exit=run.exit, outputs=run.outputs, error=run.error,
                   created_at=run.created_at, started_at=run.started_at, finished_at=run.finished_at,
                   duration_ms=run.duration_ms, usage=run.usage, metadata=run.metadata, links=links(run.run_id))

    # --- execution ------------------------------------------------------------------------------------------------------

    def _execute(self, run: RunState) -> None:
        t0 = time.perf_counter()
        keep_in = None
        try:
            result = self.executor.run(run.inputs, run_id=run.run_id, cassette_mode="live", metadata=run.metadata,
                                       on_event=lambda event: self._fan_out(run, event))
            exit, outputs = result.exit, result.outputs
            error = result.error.model_dump(mode="json") if result.error is not None else None
            duration_ms, usage = result.duration_ms, result.usage.model_dump(mode="json")
            keep_in = _local_dir(result.workspace)
        except Exception as err:  # noqa: BLE001 — the run fails; the supervisor keeps serving
            failure = ProcessError(run_id=run.run_id, process=self.plan.root, step=None, cause="internal",
                                   message=f"{type(err).__name__}: {err}", inputs=run.inputs)
            error = failure.model_dump(mode="json")
            exit, outputs, usage = RESERVED_EXIT, {"error": error}, None
            duration_ms = (time.perf_counter() - t0) * 1000
        release_files(run.uploads, keep_in / ".wynd" / "uploads" if keep_in else None)
        status = "failed" if exit == RESERVED_EXIT else "succeeded"
        with run.cond:
            run.exit, run.outputs, run.error, run.usage, run.duration_ms = exit, outputs, error, usage, duration_ms
            run.started_at = run.started_at or _now()
            run.finished_at = _now()
            run.status = status
            run.push("end", {"status": status, "exit": exit})
        with self._lock:
            self._finished.append(run.run_id)
            while len(self._finished) > self.retention:
                self._runs.pop(self._finished.pop(0), None)
            self._idle.notify_all()

    def _fan_out(self, run: RunState, event: dict[str, Any]) -> None:
        with run.cond:
            run.push("trace", event)
            if event.get("type") == "run.start":
                run.status = "running"
                run.started_at = _now()
                run.push("status", {"status": "running"})

    def _active(self) -> bool:
        return any(not run.terminal for run in self._runs.values())


def _fingerprint(request: RunRequest) -> str:
    body = {"inputs": request.inputs, "files": {k: v.content_base64 for k, v in request.files.items()}}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def _local_dir(uri: str | None) -> Path | None:
    if uri is None:
        return None
    parsed = urlparse(uri)
    return Path(url2pathname(parsed.path)) if parsed.scheme == "file" else None


def _now() -> datetime:
    return datetime.now(UTC)

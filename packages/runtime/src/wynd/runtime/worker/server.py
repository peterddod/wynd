"""The worker side of the venv protocol: stdio claim, per-run output capture, `init` snapshot verification and
method dispatch incl. `edge.check` (PLAN §3.12, §3.6; `$DRAFTS/02 §5.1`).

The protocol runs on private duplicates of the original stdin/stdout. Fd 0 becomes /dev/null and fd 1 points at
stderr, so `print()`, C-level writes and child processes can never corrupt it. During `run_step`, fds 1 and 2 are
redirected to a temporary file whose tail is sent as one `step.log` (stream "output") notification.
"""

from __future__ import annotations

import json
import os
import platform
import signal
import sys
import tempfile
import threading
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, TextIO

from pydantic import BaseModel, ValidationError

from wynd.runtime import __version__
from wynd.runtime.handle import StepCache
from wynd.runtime.interface import describe_step, interface_of
from wynd.runtime.middleware import run_chain
from wynd.runtime.step import Step, step_kind
from wynd.runtime.summary import summarise
from wynd.runtime.worker.loader import load_step_class
from wynd.runtime.worker.protocol import (
    EDGE_CHECK_ERROR,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    NOT_INITIALISED,
    PARSE_ERROR,
    PROTOCOL_MISMATCH,
    PROTOCOL_VERSION,
    UNKNOWN_STEP,
    InitStep,
    RunStepParams,
    RunStepResult,
    StepTimings,
)
from wynd.spec.base import RESERVED_EXIT
from wynd.spec.hashing import interface_hash
from wynd.spec.records import StepError

OUTPUT_TAIL = 64 * 1024            # bytes of captured run output kept for the step.log notification
SNAPSHOT_DRIFT = "SnapshotDrift"

_active: WorkerServer | None = None


class RpcFault(Exception):
    """A JSON-RPC error response."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class WorkerServer:
    """Handles one JSON-RPC request at a time on the main thread."""

    def __init__(self, out: TextIO) -> None:
        self.out = out
        self.stopping = False
        self.initialised = False
        self.steps: dict[str, type[Step] | dict[str, Any]] = {}   # step id -> class, or its init error
        self.caches: dict[str, StepCache] = {}                     # one per step id per worker lifetime
        self._write_lock = threading.Lock()
        self._in_flight = False
        self._capture: Any = None                                  # worker-lifetime temp file for run output

    def handle_line(self, line: str) -> dict[str, Any] | None:
        """One request line -> its response (None for a JSON-RPC notification or a blank line)."""
        if not line.strip():
            return None
        try:
            request = json.loads(line)
        except ValueError as err:
            return _error(None, PARSE_ERROR, f"parse error: {err}")
        if not isinstance(request, dict):
            return _error(None, INVALID_REQUEST, "a request must be a JSON object")
        rid = request.get("id")
        method, params = request.get("method"), request.get("params", {})
        if request.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return _error(rid, INVALID_REQUEST, "a request needs jsonrpc '2.0' and a method")
        if not isinstance(params, dict):
            return _error(rid, INVALID_PARAMS, "params must be an object")
        self._in_flight = True
        try:
            reply = {"jsonrpc": "2.0", "id": rid, "result": self._dispatch(method, params)}
        except RpcFault as fault:
            reply = _error(rid, fault.code, fault.message, fault.data)
        except Exception:  # noqa: BLE001 — a runtime bug must not kill the worker
            reply = _error(rid, INTERNAL_ERROR, "internal error", {"traceback": traceback.format_exc()})
        finally:
            self._in_flight = False
        return reply if "id" in request else None

    def write(self, obj: dict[str, Any]) -> None:
        text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str)
        with self._write_lock:
            self.out.write(text + "\n")
            self.out.flush()

    def notify(self, event: dict[str, Any]) -> None:
        """Send `event` as an `event` notification; dropped when no request is in flight."""
        if self._in_flight:
            self.write({"jsonrpc": "2.0", "method": "event", "params": event})

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        match method:
            case "init":
                return self._init(params)
            case "ping":
                return {"pid": os.getpid()}
            case "shutdown":
                self.stopping = True
                return {}
            case "describe" | "run_step" | "edge.check" if not self.initialised:
                raise RpcFault(NOT_INITIALISED, "worker is not initialised: send init first")
            case "describe":
                return self._describe(params)
            case "run_step":
                return self._run_step(params)
            case "edge.check":
                return self._edge_check(params)
        raise RpcFault(METHOD_NOT_FOUND, f"unknown method {method!r}")

    def _init(self, params: dict[str, Any]) -> dict[str, Any]:
        protocol = params.get("protocol")
        if protocol != PROTOCOL_VERSION:
            raise RpcFault(
                PROTOCOL_MISMATCH, f"protocol mismatch: client speaks {protocol!r}, worker speaks {PROTOCOL_VERSION}"
            )
        entries = params.get("steps", [])
        if not isinstance(entries, list):
            raise RpcFault(INVALID_PARAMS, "steps must be a list")
        report = {}
        for entry in entries:
            step = _parse(InitStep, entry)
            report[step.id] = self._load(step)
        self.initialised = True
        return {
            "protocol": PROTOCOL_VERSION, "runtime_version": __version__, "python": platform.python_version(),
            "pid": os.getpid(), "steps": report,
        }

    def _load(self, step: InitStep) -> dict[str, Any]:
        """Import one step and verify its lock snapshots; failures are reported, never fatal."""
        try:
            cls = load_step_class(step.id, step.entrypoint, step.package_dir)
        except (Exception, SystemExit) as err:
            error = {
                "type": type(err).__name__, "message": str(err), "traceback": "".join(traceback.format_exception(err)),
            }
        else:
            drift = snapshot_drift(cls, step)
            if not drift:
                self.steps[step.id] = cls
                self.caches.setdefault(step.id, StepCache())
                return {"ok": True}
            error = {
                "type": SNAPSHOT_DRIFT,
                "message": f"interface snapshot drift for step {step.id}: run wynd validate --sync-interfaces",
                "traceback": None,
                "drift": drift,
            }
        self.steps[step.id] = error
        return {"ok": False, "error": error}

    def _describe(self, params: dict[str, Any]) -> dict[str, Any]:
        ids = params.get("ids")
        if ids is None:
            ids = list(self.steps)
        elif not (isinstance(ids, list) and all(isinstance(i, str) for i in ids)):
            raise RpcFault(INVALID_PARAMS, "ids must be a list of step ids or null")
        described = {}
        for step_id in ids:
            match self._step(step_id):
                case dict() as error:
                    described[step_id] = {"error": error}
                case cls:
                    described[step_id] = describe_step(cls, step_id).model_dump(mode="json")
        return {"steps": described}

    def _run_step(self, params: dict[str, Any]) -> dict[str, Any]:
        run = _parse(RunStepParams, params)
        match self._step(run.step_id):
            case dict() as error:
                return import_error(run, error).model_dump(mode="json")
            case cls:
                with self._captured_output():
                    result = run_chain(cls, run, emit=self.notify, cache=self.caches[run.step_id])
                return result.to_result().model_dump(mode="json")

    def _edge_check(self, params: dict[str, Any]) -> dict[str, Any]:
        from wynd.runtime.edges import handle_edge_check
        from wynd.runtime.executor.edges import EdgeCheckError

        try:
            return handle_edge_check(params)
        except EdgeCheckError as err:
            raise RpcFault(EDGE_CHECK_ERROR, err.message, {"cause": err.cause, "message": err.message}) from err

    def _step(self, step_id: str) -> type[Step] | dict[str, Any]:
        entry = self.steps.get(step_id)
        if entry is None:
            raise RpcFault(UNKNOWN_STEP, f"unknown step {step_id!r}: not initialised in this worker")
        return entry

    @contextmanager
    def _captured_output(self) -> Iterator[None]:
        """Point fds 1 and 2 at the capture file for one run; afterwards send its tail as one step.log."""
        if self._capture is None:
            self._capture = tempfile.TemporaryFile(buffering=0)
        fd = self._capture.fileno()
        _flush_std()
        saved = os.dup(1), os.dup(2)
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        try:
            yield
        finally:
            _flush_std()
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])
            size = os.fstat(fd).st_size
            start = max(0, size - OUTPUT_TAIL)
            text = os.pread(fd, size - start, start).decode("utf-8", errors="replace")
            if text.strip():
                self.notify({"type": "step.log", "level": "INFO", "stream": "output", "message": text})


def snapshot_drift(cls: type[Step], step: InitStep) -> list[str]:
    """The lock snapshots (PLAN §3.6) that differ from the class: "kind", "interface", "context", "exit_codes".
    An unset snapshot (`interface_hash` or shell `exit_codes` None) is not checked."""
    drift = []
    kind = step_kind(cls)
    if kind != step.kind:
        drift.append("kind")
    if step.interface_hash is not None and interface_hash(interface_of(cls).interface) != step.interface_hash:
        drift.append("interface")
    if list(getattr(cls, "context", [])) != step.context:
        drift.append("context")
    if kind == "shell" and step.exit_codes is not None:
        if {str(code): exit for code, exit in cls.exit_codes.items()} != step.exit_codes:
            drift.append("exit_codes")
    return drift


def import_error(run: RunStepParams, error: dict[str, Any]) -> RunStepResult:
    """The `error` exit (cause `import`) of a run of a step whose `init` failed."""
    now = datetime.now(UTC)
    message = error["message"] if error["type"] == SNAPSHOT_DRIFT else f"{error['type']}: {error['message']}"
    output = StepError(
        cause="import", message=message, type=error["type"], traceback=error["traceback"], inputs=run.inputs,
        attempts=0,
    )
    outputs = output.model_dump(mode="json")
    outputs.pop("exit")
    return RunStepResult(
        exit=RESERVED_EXIT, outputs=outputs, summary=summarise(run.step_path, RESERVED_EXIT, outputs), attempts=0,
        timings=StepTimings(started_at=now, ended_at=now, worker_ms=0.0),
    )


def notify(event: dict[str, Any]) -> None:
    """Send `event` as a notification of the request in flight in this worker process. For handlers that have no
    `emit` of their own (`edge.check` -> `wynd.runtime.edges.handle_edge_check`); a no-op outside a worker."""
    if _active is not None:
        _active.notify(event)


def claim_stdio() -> tuple[TextIO, TextIO]:
    """Keep private duplicates of fds 0/1 for the protocol; fd 0 -> /dev/null, fd 1 -> stderr."""
    in_fd, out_fd = os.dup(0), os.dup(1)                   # PEP 446: duplicates are not inherited by children
    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)
    os.dup2(2, 1)
    sys.stdout.reconfigure(line_buffering=True)
    return (
        os.fdopen(in_fd, "r", encoding="utf-8", errors="replace", newline="\n"),
        os.fdopen(out_fd, "w", encoding="utf-8", newline="\n"),
    )


def main() -> None:
    """Entry point of `python -m wynd.runtime.worker`: claim stdio, serve requests until EOF or `shutdown`."""
    global _active
    signal.signal(signal.SIGINT, signal.SIG_IGN)            # the parent owns shutdown (EOF / "shutdown")
    proto_in, proto_out = claim_stdio()
    server = _active = WorkerServer(proto_out)
    for line in proto_in:
        reply = server.handle_line(line)
        if reply is not None:
            server.write(reply)
        if server.stopping:
            break
    sys.exit(0)


def _parse[M: BaseModel](model: type[M], value: Any) -> M:
    try:
        return model.model_validate(value)
    except ValidationError as err:
        raise RpcFault(INVALID_PARAMS, f"invalid {model.__name__}: {err}") from err


def _error(rid: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": error}


def _flush_std() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass

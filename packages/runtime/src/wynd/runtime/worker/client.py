"""The client side of the venv protocol: spawn, `init`, calls with notifications, kill (PLAN §3.12;
`$DRAFTS/02 §5.4–§5.5`).

A daemon thread reads the worker's protocol stdout into a queue (None at EOF); `call` writes one request and waits
for its reply, handing `event` notifications to `on_event` on the calling thread. One call at a time per client:
the pool's per-venv lock guarantees it.
"""

from __future__ import annotations

import itertools
import json
import logging
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from typing import IO, TYPE_CHECKING, Any

from wynd.runtime import __version__
from wynd.runtime.worker.protocol import PROTOCOL_VERSION

if TYPE_CHECKING:
    from wynd.runtime.worker.protocol import InitStep

WORKER_MODULE = "wynd.runtime.worker"

log = logging.getLogger("wynd.runtime.worker")


class WorkerCrashed(RuntimeError):
    """The worker process exited (EOF on its protocol stdout) during a call or `init`."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class StepTimeout(TimeoutError):
    """No response within the call's timeout; the pool kills the worker."""


class WorkerRpcError(RuntimeError):
    """A JSON-RPC error response (protocol fault, or `edge.check` failure with code -32001)."""

    def __init__(self, message: str, *, code: int, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class WorkerClient:
    """One worker subprocess: `[python, "-m", "wynd.runtime.worker"]` with `env + PYTHONUNBUFFERED=1`."""

    def __init__(self, python: str, steps: list[InitStep], *, env: Mapping[str, str], cwd: str | None = None) -> None:
        self.python = python
        self.steps = list(steps)
        self.env = {**env, "PYTHONUNBUFFERED": "1"}
        self.cwd = cwd
        self.proc: subprocess.Popen[bytes] | None = None
        self.init_result: dict[str, Any] | None = None
        self._replies: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._ids = itertools.count(1)
        self._init_id: int | None = None
        self._eof = False

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc is not None else None

    @property
    def alive(self) -> bool:
        return self.proc is not None and not self._eof and self.proc.poll() is None

    def spawn(self) -> None:
        """Start the process and send `init` without waiting for the reply (a pool spawns several workers, then
        collects their inits with `start`). Idempotent. Raises `WorkerCrashed`."""
        if self.proc is not None:
            return
        try:
            self.proc = subprocess.Popen(
                [self.python, "-m", WORKER_MODULE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=self.env,
                cwd=self.cwd,
            )
        except OSError as err:
            raise WorkerCrashed(f"cannot start worker {self.python}: {err}") from err
        reader = threading.Thread(target=self._read, args=(self.proc.stdout,), name=f"wynd-worker-{self.pid}")
        reader.daemon = True
        reader.start()
        steps = [step.model_dump(mode="json") for step in self.steps]
        params = {"protocol": PROTOCOL_VERSION, "runtime_version": __version__, "steps": steps}
        self._init_id = self._send("init", params)

    def start(self, timeout: float | None = None) -> dict[str, Any]:
        """Spawn (unless `spawn` ran) and wait for the `init` reply; returns the init result. Raises `WorkerCrashed`,
        `StepTimeout`, `WorkerRpcError` (e.g. protocol mismatch)."""
        if self.init_result is not None:
            return self.init_result
        self.spawn()
        result = self._wait(self._init_id, "init", _ignore, timeout)
        if result.get("runtime_version") != __version__:
            log.warning(
                "worker %s (pid %s) runs wynd-runtime %s; this process runs %s",
                self.python, self.pid, result.get("runtime_version"), __version__,
            )
        self.init_result = result
        return result

    def call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        on_event: Callable[[dict[str, Any]], None],
        timeout: float | None,
    ) -> dict[str, Any]:
        """Send one request; route `event` notifications to `on_event`. Raises `WorkerCrashed`, `StepTimeout`,
        `WorkerRpcError`."""
        return self._wait(self._send(method, params), method, on_event, timeout)

    def kill(self) -> None:
        """Terminate, wait 2 s, then kill."""
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self._close_stdin()

    def close(self) -> None:
        """`shutdown`, wait 2 s, else kill."""
        if self.proc is None:
            return
        if self.alive:
            try:
                self.call("shutdown", {}, on_event=_ignore, timeout=2)
            except (WorkerCrashed, StepTimeout, WorkerRpcError):
                pass
        self._close_stdin()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.kill()

    def _send(self, method: str, params: dict[str, Any]) -> int:
        if self.proc is None:
            raise WorkerCrashed(f"worker {self.python} is not running: call start() first")
        if self._eof:
            raise self._crashed(method)
        rid = next(self._ids)
        request = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        data = json.dumps(request, separators=(",", ":"), ensure_ascii=False, default=str).encode() + b"\n"
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except (OSError, ValueError) as err:          # broken pipe: the worker is gone
            raise self._crashed(method) from err
        return rid

    def _wait(
        self, rid: int | None, method: str, on_event: Callable[[dict[str, Any]], None], timeout: float | None
    ) -> dict[str, Any]:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            try:
                message = self._replies.get(timeout=remaining)
            except queue.Empty:
                raise StepTimeout(
                    f"worker {self.python} (pid {self.pid}) did not answer {method} within {timeout:g}s"
                ) from None
            if message is None:
                self._eof = True
                raise self._crashed(method)
            if "id" not in message:
                if message.get("method") == "event":
                    _deliver(on_event, message.get("params") or {})
                continue
            if message["id"] != rid:                   # the reply to an abandoned call
                continue
            error = message.get("error")
            if error is not None:
                raise WorkerRpcError(error.get("message", ""), code=error.get("code"), data=error.get("data"))
            return message.get("result")

    def _read(self, stream: IO[bytes]) -> None:
        try:
            for line in stream:
                try:
                    message = json.loads(line)
                except ValueError:
                    log.warning("worker %s wrote a non-JSON protocol line: %r", self.pid, line[:200])
                    continue
                if isinstance(message, dict):
                    self._replies.put(message)
        except (OSError, ValueError):
            pass
        finally:
            self._replies.put(None)

    def _crashed(self, method: str) -> WorkerCrashed:
        returncode = None
        if self.proc is not None:
            try:
                returncode = self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        return WorkerCrashed(
            f"worker {self.python} (pid {self.pid}) exited with code {returncode} during {method}; "
            "its stderr has the traceback",
            returncode=returncode,
        )

    def _close_stdin(self) -> None:
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass


def _deliver(on_event: Callable[[dict[str, Any]], None], event: dict[str, Any]) -> None:
    """A failing observer must not abandon the call mid-protocol."""
    try:
        on_event(event)
    except Exception:  # noqa: BLE001
        log.exception("on_event failed for a %s notification", event.get("type"))


def _ignore(event: dict[str, Any]) -> None:
    return None

"""`RunApiClient`, the only run-API client (stdlib urllib + SSE parser; PLAN §3.17; `$DRAFTS/03 §13.6`)."""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import quote, urlencode

from wynd.runtime.supervisor.schema import ProcessInfo, Run, RunCreated

MAX_RECONNECTS = 3


class RunApiError(Exception):
    """A run API error response (`status`, `code` from the error body) or an unreachable server (`status` 0)."""

    def __init__(self, message: str, *, status: int, code: str, body: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.body = body or {}


class RunApiClient:
    def __init__(self, base_url: str, *, token: str | None = None, timeout_s: float = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s

    def health(self) -> bool:
        try:
            return self._call("GET", "/healthz").get("status") == "ok"
        except RunApiError:
            return False

    def ready(self) -> dict[str, Any]:
        """The `/readyz` body when ready or degraded; RunApiError(503, code = "starting" | "draining") otherwise."""
        return self._call("GET", "/readyz")

    def wait_ready(self, timeout: float = 120.0, interval: float = 0.25) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.ready()
                return
            except RunApiError as err:
                if time.monotonic() + interval > deadline:
                    raise RunApiError(f"run API at {self.base_url} not ready after {timeout:g}s: {err}",
                                      status=err.status, code=err.code, body=err.body) from None
            time.sleep(interval)

    def info(self) -> ProcessInfo:
        return ProcessInfo.model_validate(self._call("GET", "/v1/info"))

    def submit(
        self,
        inputs: dict[str, Any],
        *,
        run_id: str | None = None,
        files: dict[str, bytes] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RunCreated:
        body: dict[str, Any] = {"inputs": inputs, "metadata": metadata or {}}
        if run_id is not None:
            body["run_id"] = run_id
        if files:
            body["files"] = {name: {"content_base64": base64.b64encode(data).decode()} for name, data in files.items()}
        return RunCreated.model_validate(self._call("POST", "/v1/runs", body))

    def get(self, run_id: str, *, wait: float | None = None) -> Run:
        query = f"?{urlencode({'wait': wait})}" if wait else ""
        timeout = self.timeout_s + (wait or 0)
        return Run.model_validate(self._call("GET", f"/v1/runs/{quote(run_id)}{query}", timeout=timeout))

    def outputs(self, run_id: str) -> dict[str, Any]:
        """`{"exit", "outputs", "error"}`; RunApiError(409, "not_finished") while the run is going."""
        return self._call("GET", f"/v1/runs/{quote(run_id)}/outputs")

    def events(self, run_id: str, *, after: int | None = None) -> Iterator[tuple[int, str, dict[str, Any]]]:
        """`(id, event, data)` for `status` / `trace` / `end` events until the server closes the stream."""
        query = f"?{urlencode({'after': after})}" if after is not None else ""
        request = self._request("GET", f"/v1/runs/{quote(run_id)}/events{query}", None)
        request.add_header("Accept", "text/event-stream")
        with self._open(request, self.timeout_s) as response:
            yield from parse_sse(response)

    def run(
        self,
        inputs: dict[str, Any],
        *,
        run_id: str | None = None,
        files: dict[str, bytes] | None = None,
        metadata: dict[str, Any] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> Run:
        """Submit, follow the event stream (resuming after a dropped connection) until `end`, then fetch the run.
        `on_event` receives each trace event."""
        created = self.submit(inputs, run_id=run_id, files=files, metadata=metadata)
        last, failures = None, 0
        while True:
            ended = False
            try:
                for index, event, data in self.events(created.run_id, after=last):
                    if last is not None and index <= last:
                        continue                # already seen (a server that ignored `after`)
                    last, failures = index, 0
                    if event == "trace" and on_event is not None:
                        on_event(data)
                    if event == "end":
                        ended = True
            except (RunApiError, OSError) as err:
                if isinstance(err, RunApiError) and err.status != 0:
                    raise
            if ended:
                return self.get(created.run_id)
            failures += 1
            if failures > MAX_RECONNECTS:
                raise RunApiError(f"lost the event stream of run {created.run_id}", status=0, code="stream_lost")

    # --- transport ------------------------------------------------------------------------------------------------------

    def _call(self, method: str, path: str, body: Any = None, *, timeout: float | None = None) -> dict[str, Any]:
        request = self._request(method, path, body)
        with self._open(request, timeout or self.timeout_s) as response:
            return json.loads(response.read() or b"{}")

    def _request(self, method: str, path: str, body: Any) -> urllib.request.Request:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.base_url + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        return request

    def _open(self, request: urllib.request.Request, timeout: float) -> Any:
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as err:
            raise _api_error(err) from None
        except (urllib.error.URLError, OSError) as err:
            reason = getattr(err, "reason", err)
            raise RunApiError(f"run API at {self.base_url} unreachable: {reason}", status=0,
                              code="unreachable") from None


def parse_sse(lines: Any) -> Iterator[tuple[int, str, dict[str, Any]]]:
    """Parse an SSE byte-line stream into `(id, event, data)`; comments (`: ping`) are skipped."""
    index, event, data = -1, "message", []
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                yield index, event, json.loads("\n".join(data))
            event, data = "message", []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        match name:
            case "id":
                index = int(value)
            case "event":
                event = value
            case "data":
                data.append(value)


def _api_error(err: urllib.error.HTTPError) -> RunApiError:
    raw = err.read()
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        body = {"raw": raw.decode("utf-8", "replace")}
    detail = body.get("error") if isinstance(body.get("error"), dict) else None
    if detail is not None:
        code, message = str(detail.get("code", "error")), str(detail.get("message", ""))
    else:
        code, message = str(body.get("status", "http_error")), json.dumps(body)
    return RunApiError(f"HTTP {err.code} {code}: {message}", status=err.code, code=code, body=body)

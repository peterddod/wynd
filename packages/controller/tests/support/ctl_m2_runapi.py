"""CTL-M2 test doubles: a fake run API (`FakeRunApi`, whose instances are the `run_api_client` factory) and a fake
Docker (`FakeDocker`, installed over `wynd.controller.docker`).

`FakeRunApi` serves every URL: `ready` holds the URLs whose `/readyz` answers (`FakeDocker` adds a container's URL
when it starts one); `submits` records every `POST /v1/runs` body (plus the client's token); each submitted run ends
in `exit` with `outputs` after the trace events of `trace_events(run_id, inputs)`; `drop_after` makes the first event
stream end without `end` after that many frames (the mirror must resume); `refuse` makes `submit` raise that
`RunApiError`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from wynd.runtime.supervisor.client import RunApiError
from wynd.runtime.supervisor.schema import Links, Run, RunCreated

T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def trace_events(run_id: str, inputs: dict[str, Any], *, exit: str = "done", outputs: dict | None = None) -> list[dict]:
    """A minimal §3.13 trace of a one-step run (`read`)."""
    def event(seq: int, type: str, **fields: Any) -> dict:
        return {"v": 1, "seq": seq, "ts": "2026-09-27T12:00:00.000Z", "run_id": run_id, "type": type, **fields}

    return [
        event(1, "run.start", process="p1", mode="image", inputs=inputs, commit="c" * 40, runtime_version="0.1.0",
              workspace="file:///var/lib/wynd/workspaces/" + run_id, cassette="live", metadata={}),
        event(2, "step.start", step="read", name="read", process="p1", span=2, parent=None, id="p1#read",
              kind="deterministic", run=1, role="node", via=None, inputs=inputs, venv="v1"),
        event(3, "step.end", step="read", span=2, parent=None, run=1, kind="deterministic", exit=exit,
              timed_out=False, outputs=outputs or {}, summary={}, attempts=1, validation_failures=0, timings={},
              usage=None, model=None, replayed=False),
        event(4, "run.end", exit=exit, outputs=outputs or {}, error=None, status="succeeded", duration_ms=12.5,
              workspace_kept=False, workspace=None, usage={}, finally_errors=[]),
    ]


class FakeRunApi:
    def __init__(self, *, exit: str = "done", outputs: dict | None = None) -> None:
        self.exit = exit
        self.outputs = outputs if outputs is not None else {"words": 2}
        self.ready: set[str] = set()
        self.submits: list[dict[str, Any]] = []
        self.clients: list[FakeRunApiClient] = []
        self.drop_after: int | None = None
        self.refuse: RunApiError | None = None
        self.runs: dict[str, dict[str, Any]] = {}
        self.streams = 0

    def __call__(self, url: str) -> FakeRunApiClient:
        client = FakeRunApiClient(self, url)
        self.clients.append(client)
        return client

    def frames(self, run_id: str) -> list[tuple[int, str, dict]]:
        run = self.runs[run_id]
        events = trace_events(run_id, run["inputs"], exit=self.exit, outputs=self.outputs)
        frames = [(1, "status", {"status": "running"})]
        frames += [(i + 2, "trace", ev) for i, ev in enumerate(events)]
        frames.append((len(frames) + 1, "end", {"status": "succeeded"}))
        return frames


class FakeRunApiClient:
    def __init__(self, api: FakeRunApi, url: str) -> None:
        self.api = api
        self.base_url = url
        self.token: str | None = None
        self.timeout_s = 30.0

    def _unreachable(self) -> RunApiError:
        return RunApiError(f"run API at {self.base_url} unreachable", status=0, code="unreachable")

    def ready(self) -> dict[str, Any]:
        if self.base_url not in self.api.ready:
            raise self._unreachable()
        return {"status": "ready", "workers": []}

    def wait_ready(self, timeout: float = 120.0, interval: float = 0.25) -> None:
        if self.base_url not in self.api.ready:
            raise RunApiError(f"run API at {self.base_url} not ready after {timeout:g}s", status=0,
                              code="unreachable")

    def submit(self, inputs, *, run_id=None, files=None, metadata=None) -> RunCreated:
        if self.api.refuse is not None:
            raise self.api.refuse
        self.api.submits.append({"url": self.base_url, "inputs": inputs, "run_id": run_id, "files": dict(files or {}),
                                 "metadata": metadata, "token": self.token})
        self.api.runs[run_id] = {"inputs": inputs, "metadata": metadata or {}}
        links = Links(self=f"/v1/runs/{run_id}", events=f"/v1/runs/{run_id}/events", outputs="")
        return RunCreated(run_id=run_id, status="queued", links=links)

    def events(self, run_id: str, *, after: int | None = None):
        self.api.streams += 1
        frames = [f for f in self.api.frames(run_id) if after is None or f[0] > after]
        if self.api.drop_after is not None:
            frames, self.api.drop_after = frames[: self.api.drop_after], None
        yield from frames

    def get(self, run_id: str, *, wait: float | None = None) -> Run:
        run = self.api.runs[run_id]
        failed = self.api.exit == "error"
        return Run(
            run_id=run_id, process="p1", commit="c" * 40, runtime_version="0.1.0", mode="image",
            status="failed" if failed else "succeeded", exit=self.api.exit, outputs=self.api.outputs,
            error=None, created_at=T0, started_at=T0, finished_at=T0, duration_ms=12.5,
            usage={"input_tokens": 1, "output_tokens": 2, "cache_read_tokens": 0, "cache_write_tokens": 0,
                   "cost_usd": None, "latency_ms": 3.0, "calls": 1},
            metadata=run["metadata"], links=Links(self="", events="", outputs=""),
        )


class FakeDocker:
    """Replaces the functions of `wynd.controller.docker`; `calls` lists `(function, args)` in order."""

    def __init__(self, api: FakeRunApi, labels: dict[str, dict[str, str]] | None = None) -> None:
        self.api = api
        self.labels = labels or {}
        self.calls: list[tuple[str, Any]] = []
        self.containers: dict[str, dict[str, Any]] = {}
        self.ports: dict[str, int] = {}
        self.becomes_ready = True
        self._next_port = 50000

    def install(self, monkeypatch) -> FakeDocker:
        import wynd.controller.docker as docker

        for name in ("image_labels", "run_detached", "host_port", "rm", "logs_tail"):
            monkeypatch.setattr(docker, name, getattr(self, name))
        return self

    def image_labels(self, image: str) -> dict[str, str]:
        self.calls.append(("image_labels", image))
        return dict(self.labels.get(image, {}))

    def run_detached(self, image, *, name, env, host_port, container_port, labels, restart=None) -> str:
        self.calls.append(("run_detached", {"image": image, "name": name, "env": dict(env), "host_port": host_port,
                                            "container_port": container_port, "labels": dict(labels)}))
        self._next_port += 1
        self.ports[name] = host_port or self._next_port
        self.containers[name] = {"image": image, "env": dict(env)}
        if self.becomes_ready:
            self.api.ready.add(f"http://127.0.0.1:{self.ports[name]}")
        return f"id-{name}"

    def host_port(self, name: str, container_port: int) -> int:
        self.calls.append(("host_port", name))
        return self.ports[name]

    def rm(self, name: str) -> None:
        self.calls.append(("rm", name))
        self.containers.pop(name, None)
        port = self.ports.pop(name, None)
        if port is not None:
            self.api.ready.discard(f"http://127.0.0.1:{port}")

    def logs_tail(self, name: str, n: int = 50) -> str:
        self.calls.append(("logs_tail", name))
        return "supervisor: env-check failed: RECORDS_DIR missing"

    def started(self) -> list[dict[str, Any]]:
        return [args for fn, args in self.calls if fn == "run_detached"]

    def removed(self) -> list[str]:
        return [args for fn, args in self.calls if fn == "rm"]

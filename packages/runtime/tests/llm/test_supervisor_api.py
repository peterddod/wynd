"""Run API over a real `ThreadingHTTPServer` on port 0 with a stub executor (PLAN §3.17; `$DRAFTS/03 §13`, §16
test_supervisor_api): idempotency, 409/413/422/429/401/503, long-poll, outputs, SSE framing, readiness, drain,
`files` + `$file` substitution and cleanup, retention."""

from __future__ import annotations

import base64
import http.client
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from wynd.runtime.executor import Executor, ProcessResult
from wynd.runtime.supervisor import http as http_mod
from wynd.runtime.supervisor.files import UploadError, materialise_files, substitute_files
from wynd.runtime.supervisor.http import ROUTES, make_server
from wynd.runtime.supervisor.runs import RunManager
from wynd.runtime.trace import TraceEmitter
from wynd.runtime.usage import Usage
from wynd.spec.records import ProcessError

PROCESS = """
kind: process
name: p
goal: Echo the text.
entry: a
inputs: {text: string, pdf: "path?", pages: "list[path]?"}
outputs: {text: string}
steps: {a: {use: ./steps/a}}
edges:
  - from: a.done
    to: $exit.done
    with: {text: steps.a.outputs.text}
"""
WORKERS = [{"venv": "test", "pid": 4242, "alive": True}]


def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.01)


class StubExecutor:
    """Scripted `Executor`: real input validation, a `run.start` / `step.*` / `run.end` trace, optional hold per run."""

    def __init__(self, plan: Any, stores: Any, root: Path) -> None:
        self.plan = plan
        self.stores = stores
        self.root = root
        self._validator = Executor(plan, None, stores, env={})
        self.hold = False
        self.gates: dict[str, threading.Event] = {}
        self.exit = "done"
        self.keep_workspace = False
        self.fail_with: Exception | None = None
        self.seen: dict[str, dict[str, Any]] = {}

    def validate_inputs(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return self._validator.validate_inputs(inputs)

    def release(self, run_id: str | None = None) -> None:
        for rid, gate in self.gates.items():
            if run_id is None or rid == run_id:
                gate.set()

    def run(self, inputs, *, run_id, cassette_mode, metadata, on_event) -> ProcessResult:
        uploads = {key: Path(value).read_bytes() for key, value in inputs.items() if key == "pdf" and value}
        self.seen[run_id] = {"inputs": inputs, "metadata": metadata, "cassette_mode": cassette_mode,
                             "uploads": uploads}
        gate = self.gates.setdefault(run_id, threading.Event())
        if not self.hold:
            gate.set()
        emitter = TraceEmitter(run_id, self.stores.traces, on_event)
        emitter.emit("run.start", process="p", mode="image", inputs=inputs, metadata=metadata)
        gate.wait(10)
        if self.fail_with is not None:
            raise self.fail_with
        emitter.emit("step.start", step="a", name="a", process="p", parent=None, inputs=inputs)
        workspace = None
        if self.exit == "error":
            error = ProcessError(run_id=run_id, process="p", step="a", cause="step_error", message="boom")
            outputs = {"error": error.model_dump(mode="json")}
            if self.keep_workspace:
                path = self.root / "workspaces" / run_id
                path.mkdir(parents=True)
                workspace = path.as_uri()
                error.workspace = workspace
        else:
            error, outputs = None, {"text": inputs["text"]}
        emitter.emit("step.end", step="a", exit=self.exit, outputs=outputs)
        status = "failed" if self.exit == "error" else "succeeded"
        emitter.emit("run.end", exit=self.exit, outputs=outputs, status=status)
        return ProcessResult(run_id=run_id, process="p", exit=self.exit, outputs=outputs, error=error, status=status,
                             trace="file:///trace", workspace=workspace, duration_ms=12.5,
                             usage=Usage(input_tokens=3, output_tokens=2, calls=1))


@dataclass
class Resp:
    status: int
    headers: dict[str, str]
    text: str

    @property
    def json(self) -> Any:
        return json.loads(self.text)


@dataclass
class Api:
    host: str
    port: int
    manager: RunManager
    stub: StubExecutor
    server: Any
    uploads: Path
    threads: list[threading.Thread] = field(default_factory=list)

    def call(self, method: str, path: str, body: Any = None, *, headers: dict[str, str] | None = None,
             raw: bytes | None = None) -> Resp:
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        all_headers = {"Content-Type": "application/json", **(headers or {})}
        conn.request(method, path, body=data, headers=all_headers)
        response = conn.getresponse()
        text = response.read().decode()
        conn.close()
        return Resp(response.status, {k.lower(): v for k, v in response.getheaders()}, text)

    def post(self, body: Any, **kw: Any) -> Resp:
        return self.call("POST", "/v1/runs", body, **kw)

    def wait_status(self, run_id: str, status: str) -> None:
        wait_until(lambda: self.manager.get(run_id).status == status)


@pytest.fixture
def api(graph, tmp_path):
    made: list[Api] = []

    def make(*, token: str | None = None, ready: bool = True, workers=lambda: WORKERS, **limits: Any) -> Api:
        g = graph(PROCESS)
        stub = StubExecutor(g.plan, g.stores, tmp_path)
        manager = RunManager(stub, g.stores, uploads_dir=tmp_path / "uploads", workers=workers, **limits)
        if ready:
            manager.mark_ready()
        server = make_server(manager, host="127.0.0.1", port=0, token=token)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        made.append(Api("127.0.0.1", server.server_address[1], manager, stub, server, tmp_path / "uploads"))
        return made[-1]

    yield make
    for a in made:
        a.stub.hold = False
        a.stub.release()
        a.manager.drain(5)
        a.server.shutdown()
        a.server.server_close()


def parse_frames(text: str) -> tuple[list[str], list[tuple[int, str, dict]]]:
    comments, frames = [], []
    for block in text.split("\n\n"):
        if not block:
            continue
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if line.startswith(":"):
                comments.append(line)
                continue
            name, _, value = line.partition(": ")
            fields[name] = value
        if "event" in fields:
            frames.append((int(fields["id"]), fields["event"], json.loads(fields["data"])))
    return comments, frames


# --- health, readiness, info, routing ---------------------------------------------------------------------------------


def test_health_is_always_ok_and_readiness_follows_the_lifecycle(api):
    workers = [dict(w) for w in WORKERS]
    a = api(ready=False, workers=lambda: workers)
    assert a.call("GET", "/healthz").json == {"status": "ok"}
    starting = a.call("GET", "/readyz")
    assert (starting.status, starting.json) == (503, {"status": "starting"})
    assert a.post({"inputs": {"text": "x"}}).json["error"]["code"] == "not_ready"

    a.manager.mark_ready()
    ready = a.call("GET", "/readyz")
    assert (ready.status, ready.json) == (200, {"status": "ready", "workers": WORKERS})

    workers[0]["alive"] = False                       # a dead worker is respawned on the next dispatch
    degraded = a.call("GET", "/readyz")
    assert (degraded.status, degraded.json["status"]) == (200, "degraded")

    a.manager.begin_drain()
    draining = a.call("GET", "/readyz")
    assert (draining.status, draining.json) == (503, {"status": "draining"})
    assert a.call("GET", "/healthz").status == 200


def test_a_failed_env_check_keeps_the_supervisor_starting_with_the_reasons(api):
    a = api(ready=False)
    a.manager.problems = ["E-ENV-MISSING: required env var GITHUB_TOKEN is not set"]
    resp = a.call("GET", "/readyz")
    assert (resp.status, resp.json["status"], resp.json["problems"]) == (503, "starting", a.manager.problems)


def test_info_describes_the_process_and_limits(api):
    a = api(max_concurrent=2, max_queued=3, max_body_mb=5)
    info = a.call("GET", "/v1/info").json
    assert (info["api_version"], info["process"], info["name"], info["goal"]) == ("1", "p", "p", "Echo the text.")
    assert info["runtime_version"] == "0.1.0" and info["commit"] is None
    assert info["inputs_schema"]["required"] == ["text"]
    assert info["inputs_schema"]["properties"]["pdf"]["anyOf"][0] == {"type": "string", "format": "path"}
    assert list(info["outputs_schema"]) == ["done"]
    assert info["steps"] == ["p#a"]
    assert info["limits"] == {"max_concurrent_runs": 2, "max_queued_runs": 3, "max_body_mb": 5}


def test_every_route_is_served_and_unknown_paths_and_methods_are_refused(api):
    a = api()
    run_id = a.post({"inputs": {"text": "x"}, "run_id": "r1"}).json["run_id"]
    a.wait_status(run_id, "succeeded")
    for method, route in ROUTES:
        path = route.replace("{id}", run_id)
        resp = a.post({"inputs": {"text": "x"}, "run_id": "r1"}) if method == "POST" else a.call(method, path)
        assert resp.status in (200, 202), (method, route, resp.status)
    missing = a.call("GET", "/v1/nope")
    assert (missing.status, missing.json["error"]["code"]) == (404, "not_found")
    assert a.call("GET", "/v1/runs/unknown").json["error"] == {"code": "not_found", "message": "no run unknown",
                                                               "details": None}
    wrong = a.call("POST", "/v1/info", {})
    assert (wrong.status, wrong.json["error"]["code"]) == (405, "method_not_allowed")


def test_bearer_token_guards_v1_but_not_the_probes(api):
    a = api(token="s3cret")
    assert a.call("GET", "/healthz").status == 200
    assert a.call("GET", "/readyz").status == 200
    missing = a.call("GET", "/v1/info")
    assert (missing.status, missing.json["error"]["code"]) == (401, "unauthorized")
    assert missing.headers["www-authenticate"] == "Bearer"
    assert a.call("GET", "/v1/info", headers={"Authorization": "Bearer wrong"}).status == 401
    assert a.post({"inputs": {"text": "x"}}).status == 401
    assert a.call("GET", "/v1/info", headers={"Authorization": "Bearer s3cret"}).status == 200


# --- submit ------------------------------------------------------------------------------------------------------------


def test_submit_is_accepted_and_idempotent_on_run_id(api):
    a = api()
    body = {"inputs": {"text": "hello"}, "run_id": "run-1", "metadata": {"trigger": "manual", "release_id": None}}
    created = a.post(body)
    assert created.status == 202
    assert created.headers["location"] == "/v1/runs/run-1"
    assert created.json == {"run_id": "run-1", "status": "queued", "links": {
        "self": "/v1/runs/run-1", "events": "/v1/runs/run-1/events", "outputs": "/v1/runs/run-1/outputs"}}
    a.wait_status("run-1", "succeeded")

    again = a.post(body)
    assert (again.status, again.json["status"]) == (200, "succeeded")
    assert len(a.stub.seen) == 1
    assert a.stub.seen["run-1"] == {"inputs": {"text": "hello", "pdf": None, "pages": None},
                                    "metadata": {"trigger": "manual", "release_id": None},
                                    "cassette_mode": "live", "uploads": {}}

    conflict = a.post({**body, "inputs": {"text": "other"}})
    assert (conflict.status, conflict.json["error"]["code"]) == (409, "run_id_conflict")


def test_submit_without_run_id_generates_one(api):
    a = api()
    created = a.post({"inputs": {"text": "x"}})
    assert created.status == 202 and created.json["run_id"].startswith("run_")


def test_a_run_id_already_in_the_registry_conflicts(api):
    a = api()
    now = datetime.now(UTC).isoformat()
    a.manager.stores.runs.create({"id": "old-run", "kind": "run", "process": "p", "status": "succeeded",
                                  "created_at": now, "updated_at": now})
    resp = a.post({"inputs": {"text": "x"}, "run_id": "old-run"})
    assert (resp.status, resp.json["error"]["code"]) == (409, "run_id_conflict")


def test_invalid_inputs_and_requests_are_422_and_bad_json_400(api):
    a = api()
    invalid = a.post({"inputs": {"text": 5, "extra": 1}})
    assert (invalid.status, invalid.json["error"]["code"]) == (422, "invalid_inputs")
    locs = sorted(tuple(d["loc"]) for d in invalid.json["error"]["details"])
    assert locs == [("extra",), ("text",)]

    bad_id = a.post({"inputs": {"text": "x"}, "run_id": "../etc"})
    assert (bad_id.status, bad_id.json["error"]["code"]) == (422, "invalid_request")
    unknown_field = a.post({"inputs": {}, "surprise": True})
    assert (unknown_field.status, unknown_field.json["error"]["code"]) == (422, "invalid_request")

    not_json = a.post(None, raw=b"{not json")
    assert (not_json.status, not_json.json["error"]["code"]) == (400, "bad_request")
    assert not a.stub.seen and not a.manager.list()


def test_a_body_over_the_cap_is_413(api):
    a = api(max_body_mb=1)
    big = {"inputs": {"text": "x" * (1024 * 1024 + 10)}}
    resp = a.post(big)
    assert (resp.status, resp.json["error"]["code"]) == (413, "too_large")
    assert a.post({"inputs": {"text": "small"}}).status == 202


def test_the_queue_is_bounded_with_429(api):
    a = api(max_concurrent=1, max_queued=1)
    a.stub.hold = True
    first = a.post({"inputs": {"text": "1"}}).json["run_id"]
    a.wait_status(first, "running")
    second = a.post({"inputs": {"text": "2"}}).json["run_id"]
    assert a.manager.get(second).status == "queued"
    full = a.post({"inputs": {"text": "3"}})
    assert (full.status, full.json["error"]["code"]) == (429, "queue_full")

    a.stub.release(first)
    wait_until(lambda: second in a.stub.gates)
    a.stub.release(second)
    a.wait_status(second, "succeeded")
    assert a.post({"inputs": {"text": "3"}}).status == 202


# --- reading runs -----------------------------------------------------------------------------------------------------


def test_get_long_polls_until_the_run_finishes(api):
    a = api()
    a.stub.hold = True
    run_id = a.post({"inputs": {"text": "hi"}}).json["run_id"]
    a.wait_status(run_id, "running")

    t0 = time.monotonic()
    short = a.call("GET", f"/v1/runs/{run_id}?wait=0.2").json
    assert short["status"] == "running" and short["exit"] is None and short["started_at"] is not None
    assert 0.15 < time.monotonic() - t0 < 5

    threading.Timer(0.3, a.stub.release, args=(run_id,)).start()
    done = a.call("GET", f"/v1/runs/{run_id}?wait=30").json
    assert (done["status"], done["exit"], done["outputs"], done["error"]) == ("succeeded", "done", {"text": "hi"}, None)
    assert (done["process"], done["mode"], done["runtime_version"], done["api_version"]) == ("p", "local", "0.1.0", "1")
    assert done["duration_ms"] == 12.5 and done["finished_at"] is not None
    assert done["usage"] == {"input_tokens": 3, "output_tokens": 2, "cache_read_tokens": 0, "cache_write_tokens": 0,
                             "cost_usd": None, "latency_ms": 0.0, "calls": 1}
    assert done["links"]["events"] == f"/v1/runs/{run_id}/events"
    assert a.call("GET", f"/v1/runs/{run_id}?wait=abc").status == 400


def test_outputs_are_409_until_finished(api):
    a = api()
    a.stub.hold = True
    run_id = a.post({"inputs": {"text": "hi"}}).json["run_id"]
    pending = a.call("GET", f"/v1/runs/{run_id}/outputs")
    assert (pending.status, pending.json["error"]["code"]) == (409, "not_finished")
    a.stub.release(run_id)
    a.wait_status(run_id, "succeeded")
    assert a.call("GET", f"/v1/runs/{run_id}/outputs").json == {"exit": "done", "outputs": {"text": "hi"},
                                                                "error": None}


def test_an_error_exit_is_failed_with_the_process_error(api):
    a = api()
    a.stub.exit = "error"
    run_id = a.post({"inputs": {"text": "hi"}}).json["run_id"]
    a.wait_status(run_id, "failed")
    out = a.call("GET", f"/v1/runs/{run_id}/outputs").json
    assert out["exit"] == "error"
    assert (out["error"]["cause"], out["error"]["step"]) == ("step_error", "a")
    assert out["outputs"] == {"error": out["error"]}


def test_an_executor_crash_fails_the_run_with_cause_internal(api):
    a = api()
    a.stub.fail_with = RuntimeError("disk full")
    run_id = a.post({"inputs": {"text": "hi"}}).json["run_id"]
    a.wait_status(run_id, "failed")
    run = a.call("GET", f"/v1/runs/{run_id}").json
    assert (run["exit"], run["error"]["cause"], run["error"]["message"]) == ("error", "internal",
                                                                             "RuntimeError: disk full")
    _, frames = parse_frames(a.call("GET", f"/v1/runs/{run_id}/events").text)
    assert frames[-1][1:] == ("end", {"status": "failed", "exit": "error"})
    assert a.call("GET", "/readyz").json["status"] == "ready"


def test_list_is_newest_first_with_status_filter_and_limit(api):
    a = api()
    ids = []
    for n in range(3):
        ids.append(a.post({"inputs": {"text": str(n)}, "run_id": f"r{n}"}).json["run_id"])
        a.wait_status(ids[-1], "succeeded")
    a.stub.hold = True
    a.post({"inputs": {"text": "held"}, "run_id": "r3"})
    a.wait_status("r3", "running")

    runs = a.call("GET", "/v1/runs").json["runs"]
    assert [r["run_id"] for r in runs] == ["r3", "r2", "r1", "r0"]
    assert set(runs[0]) == {"run_id", "status", "exit", "created_at", "finished_at"}
    assert [r["run_id"] for r in a.call("GET", "/v1/runs?status=succeeded&limit=2").json["runs"]] == ["r2", "r1"]
    assert [r["run_id"] for r in a.call("GET", "/v1/runs?status=running").json["runs"]] == ["r3"]


def test_finished_runs_beyond_retention_are_forgotten(api):
    a = api(retention=1)
    for run_id in ("first", "second"):
        a.post({"inputs": {"text": run_id}, "run_id": run_id})
        a.wait_status(run_id, "succeeded")
    wait_until(lambda: a.manager.get("first") is None)
    assert a.call("GET", "/v1/runs/first").status == 404
    assert a.call("GET", "/v1/runs/second").status == 200


# --- SSE ----------------------------------------------------------------------------------------------------------------


def test_sse_stream_interleaves_status_and_trace_and_ends(api):
    a = api()
    run_id = a.post({"inputs": {"text": "hi"}}).json["run_id"]
    a.wait_status(run_id, "succeeded")

    resp = a.call("GET", f"/v1/runs/{run_id}/events")
    assert resp.status == 200
    assert resp.headers["content-type"] == "text/event-stream"
    assert resp.headers["cache-control"] == "no-cache"
    assert "content-length" not in resp.headers
    assert resp.text.startswith(": connected\n\n")
    _, frames = parse_frames(resp.text)
    assert [i for i, _, _ in frames] == list(range(len(frames)))
    kinds = [(event, data.get("type") or data.get("status")) for _, event, data in frames]
    assert kinds == [("status", "queued"), ("trace", "run.start"), ("status", "running"), ("trace", "step.start"),
                     ("trace", "step.end"), ("trace", "run.end"), ("end", "succeeded")]
    assert frames[-1][2] == {"status": "succeeded", "exit": "done"}
    traces = [data for _, event, data in frames if event == "trace"]
    assert [t["seq"] for t in traces] == [1, 2, 3, 4] and {t["run_id"] for t in traces} == {run_id}


def test_sse_resumes_after_an_id_or_last_event_id(api):
    a = api()
    run_id = a.post({"inputs": {"text": "hi"}}).json["run_id"]
    a.wait_status(run_id, "succeeded")
    _, after = parse_frames(a.call("GET", f"/v1/runs/{run_id}/events?after=2").text)
    assert [i for i, _, _ in after] == [3, 4, 5, 6]
    _, header = parse_frames(a.call("GET", f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": "4"}).text)
    assert [i for i, _, _ in header] == [5, 6]
    _, none = parse_frames(a.call("GET", f"/v1/runs/{run_id}/events?after=6").text)
    assert none == []


def test_sse_follows_a_live_run_with_pings_then_closes(api, monkeypatch):
    monkeypatch.setattr(http_mod, "PING_INTERVAL_S", 0.05)
    a = api()
    a.stub.hold = True
    run_id = a.post({"inputs": {"text": "hi"}}).json["run_id"]
    a.wait_status(run_id, "running")

    conn = http.client.HTTPConnection(a.host, a.port, timeout=10)
    conn.request("GET", f"/v1/runs/{run_id}/events")
    stream = conn.getresponse()
    lines = []
    while ": ping" not in lines:
        lines.append(stream.fp.readline().decode().rstrip("\n"))
    a.stub.release(run_id)
    rest = stream.read().decode()                   # returns once the server closes the stream after `end`
    conn.close()
    comments, frames = parse_frames("\n".join(lines) + "\n" + rest)
    assert comments[0] == ": connected" and ": ping" in comments
    assert frames[-1][1] == "end"
    assert [i for i, _, _ in frames] == list(range(len(frames)))


# --- drain ----------------------------------------------------------------------------------------------------------------


def test_drain_refuses_new_runs_and_waits_for_running_ones(api):
    a = api()
    a.stub.hold = True
    run_id = a.post({"inputs": {"text": "hi"}}).json["run_id"]
    a.wait_status(run_id, "running")

    result: list[bool] = []
    drainer = threading.Thread(target=lambda: result.append(a.manager.drain(10)))
    drainer.start()
    wait_until(lambda: a.manager.state == "draining")
    assert a.call("GET", "/readyz").json == {"status": "draining"}
    refused = a.post({"inputs": {"text": "late"}})
    assert (refused.status, refused.json["error"]["code"]) == (503, "not_ready")
    assert drainer.is_alive()

    a.stub.release(run_id)
    drainer.join(5)
    assert result == [True]
    assert a.manager.get(run_id).status == "succeeded"


def test_drain_gives_up_after_its_timeout(api):
    a = api()
    a.stub.hold = True
    run_id = a.post({"inputs": {"text": "hi"}}).json["run_id"]
    a.wait_status(run_id, "running")
    t0 = time.monotonic()
    assert a.manager.drain(0.2) is False
    assert time.monotonic() - t0 < 2


# --- files ------------------------------------------------------------------------------------------------------------


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_files_are_written_and_substituted_then_removed(api):
    a = api()
    body = {"inputs": {"text": "x", "pdf": {"$file": "inv1.pdf"}, "pages": [{"$file": "p1.png"}, {"$file": "p2.png"}]},
            "files": {"inv1.pdf": {"content_base64": b64(b"%PDF-1.4 hi")}, "p1.png": {"content_base64": b64(b"1")},
                      "p2.png": {"content_base64": b64(b"2")}}}
    run_id = a.post(body).json["run_id"]
    a.wait_status(run_id, "succeeded")
    seen = a.stub.seen[run_id]
    upload_dir = a.uploads / run_id
    assert seen["inputs"]["pdf"] == str(upload_dir / "inv1.pdf")
    assert seen["inputs"]["pages"] == [str(upload_dir / "p1.png"), str(upload_dir / "p2.png")]
    assert seen["uploads"] == {"pdf": b"%PDF-1.4 hi"}          # the file existed while the run ran
    assert not upload_dir.exists()                             # deleted at run end


def test_files_move_into_a_kept_workspace(api, tmp_path):
    a = api()
    a.stub.exit, a.stub.keep_workspace = "error", True
    body = {"inputs": {"text": "x", "pdf": {"$file": "inv1.pdf"}},
            "files": {"inv1.pdf": {"content_base64": b64(b"PDF")}}}
    run_id = a.post(body).json["run_id"]
    a.wait_status(run_id, "failed")
    assert not (a.uploads / run_id).exists()
    assert (tmp_path / "workspaces" / run_id / ".wynd" / "uploads" / "inv1.pdf").read_bytes() == b"PDF"


def test_unknown_file_references_and_bad_base64_are_422_and_write_nothing(api):
    a = api()
    unknown = a.post({"inputs": {"text": "x", "pdf": {"$file": "missing.pdf"}},
                      "files": {"other.pdf": {"content_base64": b64(b"x")}}})
    assert (unknown.status, unknown.json["error"]["code"]) == (422, "invalid_inputs")
    assert "missing.pdf" in unknown.json["error"]["message"]
    bad = a.post({"inputs": {"text": "x"}, "files": {"a.pdf": {"content_base64": "***"}}})
    assert (bad.status, bad.json["error"]["code"]) == (422, "invalid_inputs")
    bad_name = a.post({"inputs": {"text": "x"}, "files": {"../a.pdf": {"content_base64": b64(b"x")}}})
    assert (bad_name.status, bad_name.json["error"]["code"]) == (422, "invalid_request")
    invalid_after_upload = a.post({"inputs": {"text": 1, "pdf": {"$file": "a.pdf"}},
                                   "files": {"a.pdf": {"content_base64": b64(b"x")}}})
    assert invalid_after_upload.status == 422
    assert not a.uploads.exists() or not any(a.uploads.iterdir())


def test_substitute_files_walks_any_depth_and_leaves_lookalikes():
    paths = {"a": "/u/a", "b": "/u/b"}
    value = {"x": {"$file": "a"}, "y": [{"z": {"$file": "b"}}, 1], "keep": {"$file": "a", "other": 1}, "s": "$file"}
    assert substitute_files(value, paths) == {"x": "/u/a", "y": [{"z": "/u/b"}, 1],
                                              "keep": {"$file": "a", "other": 1}, "s": "$file"}
    with pytest.raises(UploadError, match="unknown file 'c'"):
        substitute_files({"x": {"$file": "c"}}, paths)


def test_materialise_files_writes_nothing_when_a_reference_is_unknown(tmp_path):
    with pytest.raises(UploadError):
        materialise_files({"x": {"$file": "nope"}}, {"a": {"content_base64": b64(b"1")}}, tmp_path / "up")
    assert not (tmp_path / "up").exists()
    resolved = materialise_files({"x": {"$file": "a"}}, {"a": {"content_base64": b64(b"1")}}, tmp_path / "up")
    assert resolved == {"x": str(tmp_path / "up" / "a")} and (tmp_path / "up" / "a").read_bytes() == b"1"

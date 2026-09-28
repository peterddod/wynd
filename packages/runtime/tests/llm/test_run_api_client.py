"""`RunApiClient` (PLAN §3.17; `$DRAFTS/03 §13.6`, §16 test_run_api_client): against a scripted stub server (errors →
`RunApiError`, SSE parsing across chunk boundaries, reconnect after a dropped stream, `wait_ready`, bearer token) and
end to end against the real supervisor HTTP layer + `RunManager` + `Executor` with an in-process dispatcher."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from wynd.runtime.supervisor.client import RunApiClient, RunApiError, parse_sse
from wynd.runtime.supervisor.http import make_server
from wynd.runtime.supervisor.runs import RunManager
from wynd.runtime.supervisor.schema import ProcessInfo, Run, RunCreated

# --- scripted stub server ---------------------------------------------------------------------------------------------


class StubHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.server.seen.append(("GET", self.path, dict(self.headers)))
        self.server.script[self.path.split("?")[0]](self)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.seen.append(("POST", self.path, dict(self.headers), body))
        self.server.script[self.path](self)

    def log_message(self, *args: Any) -> None:
        pass

    def reply(self, status: int, body: Any, *, raw: bytes | None = None) -> None:
        data = raw if raw is not None else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def sse(self, chunks: list[str], delay: float = 0.01) -> None:
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(chunk.encode())
            self.wfile.flush()
            time.sleep(delay)


@pytest.fixture
def stub():
    servers = []

    def make(script: dict[str, Callable[[StubHandler], None]]) -> tuple[RunApiClient, Any]:
        server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        server.daemon_threads = True
        server.script, server.seen = script, []
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        servers.append(server)
        return RunApiClient(f"http://127.0.0.1:{server.server_address[1]}/", token="tok", timeout_s=5), server

    yield make
    for server in servers:
        server.shutdown()
        server.server_close()


def frame(index: int, event: str, data: dict) -> str:
    return f"id: {index}\nevent: {event}\ndata: {json.dumps(data)}\n\n"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_error_bodies_become_run_api_errors(stub):
    details = [{"loc": ["text"], "msg": "Field required", "type": "missing"}]
    client, _ = stub({
        "/v1/runs": lambda h: h.reply(422, {"error": {"code": "invalid_inputs", "message": "bad", "details": details}}),
        "/v1/runs/r1/outputs": lambda h: h.reply(409, {"error": {"code": "not_finished", "message": "running",
                                                                 "details": None}}),
        "/v1/info": lambda h: h.reply(500, None, raw=b"<html>oops</html>"),
    })
    with pytest.raises(RunApiError) as err:
        client.submit({"text": 1})
    assert (err.value.status, err.value.code, err.value.body["error"]["details"]) == (422, "invalid_inputs", details)
    assert "HTTP 422 invalid_inputs: bad" in str(err.value)
    with pytest.raises(RunApiError) as err:
        client.outputs("r1")
    assert (err.value.status, err.value.code) == (409, "not_finished")
    with pytest.raises(RunApiError) as err:
        client.info()
    assert (err.value.status, err.value.code, err.value.body) == (500, "http_error", {"raw": "<html>oops</html>"})


def test_an_unreachable_server_is_status_0_and_unhealthy():
    client = RunApiClient(f"http://127.0.0.1:{free_port()}", timeout_s=1)
    assert client.health() is False
    with pytest.raises(RunApiError) as err:
        client.info()
    assert (err.value.status, err.value.code) == (0, "unreachable")


def test_requests_carry_the_token_and_submit_encodes_files(stub):
    created = {"run_id": "r1", "status": "queued", "links": {"self": "/v1/runs/r1", "events": "/v1/runs/r1/events",
                                                              "outputs": "/v1/runs/r1/outputs"}}
    client, server = stub({"/v1/runs": lambda h: h.reply(202, created)})
    result = client.submit({"pdf": {"$file": "a.pdf"}}, run_id="r1", files={"a.pdf": b"%PDF"},
                           metadata={"trigger": "api"})
    assert result == RunCreated.model_validate(created)
    _, path, headers, body = server.seen[0]
    assert path == "/v1/runs" and headers["Authorization"] == "Bearer tok"
    assert body == {"inputs": {"pdf": {"$file": "a.pdf"}}, "run_id": "r1", "metadata": {"trigger": "api"},
                    "files": {"a.pdf": {"content_base64": "JVBERg=="}}}


def test_sse_is_parsed_across_chunk_boundaries(stub):
    text = (": connected\n\n" + frame(0, "status", {"status": "queued"}) + ": ping\n\n"
            + "id: 1\nevent: trace\ndata: {\"seq\": 1,\ndata:  \"type\": \"run.start\"}\n\n"
            + frame(2, "end", {"status": "succeeded", "exit": "done"}))
    cuts = [3, 17, 18, 40, 41, 60, 95, 96, 97, 130, len(text)]
    chunks = [text[a:b] for a, b in zip([0, *cuts], cuts, strict=False)]
    client, server = stub({"/v1/runs/r1/events": lambda h: h.sse(chunks)})
    events = list(client.events("r1", after=None))
    assert events == [(0, "status", {"status": "queued"}), (1, "trace", {"seq": 1, "type": "run.start"}),
                      (2, "end", {"status": "succeeded", "exit": "done"})]
    assert server.seen[0][1] == "/v1/runs/r1/events"
    list(client.events("r1", after=4))
    assert server.seen[1][1] == "/v1/runs/r1/events?after=4"


def test_parse_sse_handles_crlf_and_frames_without_trailing_blank_line():
    lines = [b"id: 3\r\n", b"event: trace\r\n", b"data: {\"a\": 1}\r\n", b"\r\n", b"id: 4\n", b"data: {}\n"]
    assert list(parse_sse(lines)) == [(3, "trace", {"a": 1})]


def test_run_follows_events_resuming_after_a_dropped_stream(stub):
    created = {"run_id": "r1", "status": "queued", "links": {"self": "s", "events": "e", "outputs": "o"}}
    run = {"api_version": "1", "run_id": "r1", "process": "p", "commit": None, "runtime_version": "0.1.0",
           "mode": "image", "status": "succeeded", "exit": "done", "outputs": {"text": "hi"}, "error": None,
           "created_at": "2026-09-22T21:50:01.100Z", "started_at": None, "finished_at": None, "duration_ms": 1.0,
           "usage": None, "metadata": {}, "links": created["links"]}
    streams = iter([
        [frame(0, "status", {"status": "queued"}), frame(1, "trace", {"seq": 1, "type": "run.start"})],   # dropped
        [frame(2, "trace", {"seq": 2, "type": "run.end"}), frame(3, "end", {"status": "succeeded", "exit": "done"})],
    ])
    client, server = stub({
        "/v1/runs": lambda h: h.reply(202, created),
        "/v1/runs/r1/events": lambda h: h.sse(next(streams)),
        "/v1/runs/r1": lambda h: h.reply(200, run),
    })
    seen: list[dict] = []
    result = client.run({"text": "hi"}, on_event=seen.append)
    assert isinstance(result, Run) and (result.status, result.outputs) == ("succeeded", {"text": "hi"})
    assert [e["type"] for e in seen] == ["run.start", "run.end"]
    assert [entry[1] for entry in server.seen if entry[0] == "GET"] == [
        "/v1/runs/r1/events", "/v1/runs/r1/events?after=1", "/v1/runs/r1"]


def test_run_gives_up_when_the_stream_keeps_dropping(stub):
    created = {"run_id": "r1", "status": "queued", "links": {"self": "s", "events": "e", "outputs": "o"}}
    client, _ = stub({"/v1/runs": lambda h: h.reply(202, created),
                      "/v1/runs/r1/events": lambda h: h.sse([frame(0, "status", {"status": "queued"})])})
    with pytest.raises(RunApiError) as err:
        client.run({})
    assert err.value.code == "stream_lost"


def test_wait_ready_polls_until_ready_and_times_out_with_the_last_state(stub):
    answers = iter([(503, {"status": "starting"}), (503, {"status": "starting"}), (200, {"status": "ready",
                                                                                         "workers": []})])
    client, server = stub({"/readyz": lambda h: h.reply(*next(answers))})
    client.wait_ready(timeout=5, interval=0.01)
    assert len(server.seen) == 3

    client, _ = stub({"/readyz": lambda h: h.reply(503, {"status": "draining"})})
    with pytest.raises(RunApiError) as err:
        client.wait_ready(timeout=0.2, interval=0.02)
    assert (err.value.status, err.value.code) == (503, "draining")
    assert "not ready after 0.2s" in str(err.value)


# --- against the real supervisor ------------------------------------------------------------------------------------

PROCESS = """
kind: process
name: p
entry: shout
inputs: {text: string, note: "path?"}
outputs: {text: string}
steps: {shout: {use: ./steps/shout}}
edges:
  - from: shout.done
    to: $exit.done
    with: {text: steps.shout.outputs.text}
"""


@pytest.fixture
def supervisor(graph, make_step, tmp_path):
    from pathlib import Path

    def shout(input):
        note = Path(input.note).read_text() if input.note else ""
        return {"text": input.text.upper() + note}

    step = make_step(shout, inputs={"text": str, "note": str | None}, done={"text": str})
    g = graph(PROCESS, {"shout": step})
    manager = RunManager(g.executor(), g.stores, uploads_dir=tmp_path / "uploads", workers=lambda: [])
    manager.mark_ready()
    server = make_server(manager, host="127.0.0.1", port=0, token="t0k")
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    yield RunApiClient(f"http://127.0.0.1:{server.server_address[1]}", token="t0k", timeout_s=10), g
    manager.drain(5)
    server.shutdown()
    server.server_close()


def test_client_round_trip_against_the_supervisor(supervisor):
    client, g = supervisor
    assert client.health() is True
    assert client.ready() == {"status": "ready", "workers": []}
    client.wait_ready(timeout=1)
    info = client.info()
    assert isinstance(info, ProcessInfo) and info.process == "p" and info.steps == ["p#shout"]

    streamed: list[dict] = []
    run = client.run({"text": "hi", "note": {"$file": "n.txt"}}, run_id="rt-1", files={"n.txt": b"!"},
                     metadata={"trigger": "api"}, on_event=streamed.append)
    assert (run.run_id, run.status, run.exit, run.outputs) == ("rt-1", "succeeded", "done", {"text": "HI!"})
    assert run.metadata == {"trigger": "api"}
    assert [e["type"] for e in streamed] == [e["type"] for e in g.events("rt-1")]
    assert streamed[0]["type"] == "run.start" and streamed[-1]["type"] == "run.end"
    assert g.record("rt-1")["meta"] == {"trigger": "api"}

    assert client.outputs("rt-1") == {"exit": "done", "outputs": {"text": "HI!"}, "error": None}
    assert client.get("rt-1", wait=1).status == "succeeded"
    ids = [i for i, _, _ in client.events("rt-1", after=None)]
    assert ids == list(range(len(ids)))
    assert client.submit({"text": "hi", "note": {"$file": "n.txt"}}, run_id="rt-1", files={"n.txt": b"!"}).status \
        == "succeeded"                                          # idempotent repeat

    with pytest.raises(RunApiError) as err:
        client.submit({"text": 3})
    assert (err.value.status, err.value.code) == (422, "invalid_inputs")
    with pytest.raises(RunApiError) as err:
        RunApiClient(client.base_url).info()
    assert (err.value.status, err.value.code) == (401, "unauthorized")

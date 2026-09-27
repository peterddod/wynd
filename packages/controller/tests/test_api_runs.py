"""Run routes (PLAN §3.21 amendments 4, 11; `$DRAFTS/07 §12.1` routes 29-32, §12.5): real local runs of the `ws_basic`
fixture's `p1` (venv workers, offline) and the run event stream."""

from __future__ import annotations

import time

import pytest

from support.ctl_api_client import api_client, parse_sse

TRACE_TYPES = {"run.start", "step.start", "step.end", "edge.taken", "edge.check", "model.call", "tool.call",
               "step.log", "step.event", "process.error", "worker.start", "run.end"}


@pytest.fixture
def records_dir(tmp_path):
    return tmp_path / "records"


@pytest.fixture
def ctl(workspace, make_controller, records_dir):
    return make_controller(workspace, env={"RECORDS_DIR": str(records_dir)})


@pytest.fixture
def client(ctl):
    return api_client(ctl)


def wait_run(client, run_id: str, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in ("succeeded", "failed"):
            return run
        assert time.monotonic() < deadline, f"run {run_id} is still {run['status']}"
        time.sleep(0.05)


def test_a_local_run_through_the_api(client, ctl, records_dir):
    response = client.post("/api/runs", json={"process_id": "p1", "target": {"kind": "local"},
                                              "inputs": {"text": "hello world"}})
    assert response.status_code == 201
    started = response.json()
    assert (started["status"], started["mode"], started["target"], started["trigger"]) == (
        "running", "local", {"kind": "local"}, "api")

    run = wait_run(client, started["id"])
    assert (run["status"], run["exit"], run["error"]) == ("succeeded", "done", None)
    assert run["outputs"] == {"words": 2, "path": str(records_dir / "count.txt")}
    assert ctl.ctx.stores.runs.get(run["id"])["meta"]["trigger"] == "api"

    listed = client.get("/api/runs", params={"process_id": "p1"}).json()["runs"]
    assert [r["id"] for r in listed] == [run["id"]] and listed[0] == run
    assert client.get("/api/runs", params={"process_id": "p2"}).json() == {"runs": []}


def test_the_run_event_stream_passes_every_event_through_then_ends(client, ctl):
    started = client.post("/api/runs", json={"process_id": "p1", "inputs": {"text": "hello world"}}).json()
    response = client.get(f"/api/runs/{started['id']}/events")          # follows the run until it ends
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache" and response.headers["x-accel-buffering"] == "no"
    assert response.text.startswith("retry: 2000\n\n")

    frames = parse_sse(response.text)
    stored = ctl.runs.events(started["id"])
    assert [data for _, event, data in frames if event == "trace"] == stored     # unchanged, in seq order
    assert [frame_id for frame_id, event, _ in frames if event == "trace"] == [str(e["seq"]) for e in stored]
    assert {e["type"] for e in stored} <= TRACE_TYPES and stored[-1]["type"] == "run.end"
    assert frames[-1] == (str(stored[-1]["seq"]), "end", {})
    assert [event for _, event, _ in frames].count("end") == 1

    since = stored[-3]["seq"]
    resumed = parse_sse(client.get(f"/api/runs/{started['id']}/events", params={"since": since}).text)
    assert [data["seq"] for _, event, data in resumed if event == "trace"] == [e["seq"] for e in stored[-2:]]
    by_header = parse_sse(client.get(f"/api/runs/{started['id']}/events",
                                     headers={"Last-Event-ID": str(stored[-1]["seq"])}).text)
    assert by_header == [(str(stored[-1]["seq"]), "end", {})]


def test_a_run_that_fails_to_start_still_ends_its_stream(client, monkeypatch):
    def broken(*args, **kw):
        raise RuntimeError("no venv tool")

    monkeypatch.setattr("wynd.process.local.run_local", broken)
    started = client.post("/api/runs", json={"process_id": "p1", "inputs": {"text": "hi"}}).json()
    frames = parse_sse(client.get(f"/api/runs/{started['id']}/events").text)
    assert frames == [("0", "end", {})]
    run = client.get(f"/api/runs/{started['id']}").json()
    assert (run["status"], run["error"]["cause"]) == ("failed", "internal")
    assert "no venv tool" in run["error"]["message"]


def test_the_env_gate_refuses_before_any_run_exists(workspace, make_controller):
    ctl = make_controller(workspace, env={"RECORDS_DIR": None})
    response = api_client(ctl).post("/api/runs", json={"process_id": "p1", "inputs": {"text": "hello"}})
    assert response.status_code == 412
    error = response.json()["error"]
    assert error["code"] == "env_missing" and error["details"]["missing"] == ["RECORDS_DIR"]
    assert ctl.ctx.stores.runs.list(kind="run") == []


def test_invalid_inputs_and_an_unbuilt_image_are_refused(client, ctl):
    response = client.post("/api/runs", json={"process_id": "p1", "inputs": {"text": 3, "extra": True}})
    assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid")
    response = client.post("/api/runs", json={"process_id": "p1", "target": {"kind": "image", "commit": "a" * 40},
                                              "inputs": {"text": "hi"}})
    assert (response.status_code, response.json()["error"]["code"]) == (409, "not_built")
    response = client.post("/api/runs", json={"process_id": "p1", "target": {"kind": "release", "release_id": "r"},
                                              "inputs": {}})
    assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid")
    assert ctl.ctx.stores.runs.list(kind="run") == []


def test_unknown_runs_and_processes_are_404(client):
    for path in ("/api/runs/run_nope", "/api/runs/run_nope/events"):
        response = client.get(path)
        assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found"), path
    response = client.post("/api/runs", json={"process_id": "nope", "inputs": {}})
    assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found")


def test_list_limits_and_filters_by_release(client, ctl):
    now = "2026-09-27T12:00:00Z"
    for n, release in enumerate(["rel_a", "rel_b", None]):
        meta = {"trigger": "webhook" if release else "api", "release_id": release,
                "target": {"kind": "release" if release else "local"}}
        ctl.ctx.stores.runs.create({"id": f"run_2026092{n}", "kind": "run", "process": "p1", "status": "succeeded",
                                    "created_at": now, "updated_at": now, "mode": "image" if release else "local",
                                    "meta": meta})
    assert [r["id"] for r in client.get("/api/runs", params={"limit": 2}).json()["runs"]] == [
        "run_20260922", "run_20260921"]
    everything = client.get("/api/runs", params={"process_id": "", "release_id": ""}).json()["runs"]
    assert len(everything) == 3                                   # an empty filter is no filter
    by_release = client.get("/api/runs", params={"release_id": "rel_a"}).json()["runs"]
    assert [(r["id"], r["trigger"], r["target"]) for r in by_release] == [
        ("run_20260920", "webhook", {"kind": "release", "release_id": "rel_a"})]

"""Job routes (PLAN §3.21 amendment 3; `$DRAFTS/07 §12.1` routes 11, 12, 15-20; `$DRAFTS/06 §8.3` (+) job routes,
§7.6 job-bound chats) over a real in-process runner with the CTL-JOBS test handlers."""

from __future__ import annotations

import os

import pytest

from support.ctl_api_client import api_client, parse_sse
from support.ctl_jobs_workspace import settle
from wynd.controller.api.models_web import CompileSession, DecisionAnswer, TextAnswer
from wynd.controller.jobs.inprocess import InProcessJobRunner
from wynd.runtime.storage import stores_from_env

H = "support.ctl_jobs_handlers"
HANDLERS = {"compile": f"{H}:commit_file", "test_live": f"{H}:commit_file", "build": f"{H}:succeed",
            "bake": f"{H}:succeed", "optimise": f"{H}:succeed"}


@pytest.fixture
def make_job_controller(workspace, make_controller):
    def make(**handlers: str):
        env = dict(os.environ)
        stores = stores_from_env(env, data_dir=workspace / ".wynd")
        runner = InProcessJobRunner(env=env, workspace_root=workspace, state_dir=workspace / ".wynd", stores=stores,
                                    handlers={**HANDLERS, **handlers})
        return make_controller(workspace, runner=runner, stores=stores)

    return make


@pytest.fixture
def ctl(make_job_controller):
    return make_job_controller()


@pytest.fixture
def client(ctl):
    return api_client(ctl)


@pytest.fixture
def compile_view(monkeypatch):
    """CTL-M3's projection and answer application, faked for the test handlers' session shape."""
    calls = []

    def apply_answers(job, answers):
        calls.append(answers)
        questions = [dict(q, status="answered") for q in job.session["questions"]]
        return {**job.session, "questions": questions}, True

    monkeypatch.setattr("wynd.controller.compile_view.session_dto",
                        lambda s: CompileSession(state="done" if s.get("state") == "done" else "awaiting_input"))
    monkeypatch.setattr("wynd.controller.compile_view.apply_answers", apply_answers)
    monkeypatch.setattr("wynd.controller.compile_view.answer_text", lambda q, a: "accept")
    return calls


def finished(ctl, job_id: str) -> dict:
    return settle(ctl.ctx.runner, job_id).model_dump(mode="json")


# --- job-bound chats (06 §7.6) ----------------------------------------------------------------------------------------

@pytest.mark.parametrize(("button", "kind", "title"), [
    ("compile", "compile", "Compile p1"),
    ("build", "build", "Build p1"),
    ("test-live", "test_live", "Test p1 live"),
])
def test_a_job_button_opens_a_chat_bound_to_the_job(ctl, client, button, kind, title):
    response = client.post(f"/api/processes/p1/{button}", json={})
    assert response.status_code == 201
    job, chat = response.json()["job"], response.json()["chat"]

    assert (job["kind"], job["process_id"], job["chat_id"]) == (kind, "p1", chat["id"])
    assert chat["title"] == title
    assert (chat["job"]["id"], chat["job"]["kind"], chat["job"]["process_id"]) == (job["id"], kind, "p1")
    assert ctl.ctx.runner.status(job["id"]).chat_id == chat["id"]
    items = client.get(f"/api/chats/{chat['id']}").json()["items"]
    assert [(i["type"], i["job_id"], i["kind"]) for i in items] == [("job", job["id"], kind)]
    finished(ctl, job["id"])
    assert client.get(f"/api/jobs/{job['id']}").json()["chat_id"] == chat["id"]


def test_test_live_passes_the_steps(ctl, client):
    job = client.post("/api/processes/p1/test-live", json={"steps": ["p1#upper"]}).json()["job"]
    assert finished(ctl, job["id"])["inputs"]["steps"] == ["p1#upper"]
    job = client.post("/api/processes/p1/test-live").json()["job"]
    assert "steps" not in finished(ctl, job["id"])["inputs"]


def test_bake_answers_the_job_without_a_chat(ctl, client):
    response = client.post("/api/processes/p1/bake")
    assert response.status_code == 201
    job = response.json()
    assert (job["kind"], job["chat_id"]) == ("bake", None)
    assert client.get("/api/chats").json() == {"chats": []}


def test_a_dirty_workspace_refuses_every_job(ctl, client, workspace):
    (workspace / "processes/p1/NOTES.md").write_text("untracked\n")
    for button in ("compile", "build", "test-live", "bake"):
        response = client.post(f"/api/processes/p1/{button}", json={})
        assert response.status_code == 409, button
        error = response.json()["error"]
        assert error["code"] == "dirty_tree" and error["details"]["paths"] == ["processes/p1/NOTES.md"]
    assert client.get("/api/jobs").json() == {"jobs": []} and client.get("/api/chats").json() == {"chats": []}


def test_an_unknown_process_or_job_is_404(client):
    assert client.post("/api/processes/nope/compile", json={}).status_code == 404
    for path in ("/api/jobs/job_nope", "/api/jobs/job_nope/logs", "/api/jobs/job_nope/events",
                 "/api/jobs/job_nope/artefacts"):
        response = client.get(path)
        assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found"), path


# --- reading jobs -----------------------------------------------------------------------------------------------------

def test_list_get_logs_and_artefacts(ctl, client):
    build = client.post("/api/processes/p1/build", json={}).json()["job"]
    bake = client.post("/api/processes/p1/bake").json()
    finished(ctl, build["id"])
    finished(ctl, bake["id"])

    listed = client.get("/api/jobs", params={"process_id": "p1"}).json()["jobs"]
    assert [j["id"] for j in listed] == [bake["id"], build["id"]]
    assert client.get("/api/jobs", params={"process_id": "p2"}).json() == {"jobs": []}
    assert client.get("/api/jobs", params={"active": "true"}).json() == {"jobs": []}   # both finished, no commits

    got = client.get(f"/api/jobs/{build['id']}").json()
    assert (got["status"], got["usage"]["input_tokens"], got["artefacts"]["answer"]) == ("succeeded", 10, 42)

    logs = client.get(f"/api/jobs/{build['id']}/logs").json()
    assert f"hello from {build['id']}" in logs["text"] and logs["done"] is True
    assert logs["offset"] == len(logs["text"].encode())
    rest = client.get(f"/api/jobs/{build['id']}/logs", params={"offset": logs["offset"]}).json()
    assert rest == {"text": "", "offset": logs["offset"], "done": True}

    artefacts = client.get(f"/api/jobs/{build['id']}/artefacts").json()
    assert artefacts == {"artefacts": {"answer": 42, "cwd_is_workspace": True}}


def test_integrate_is_idempotent(ctl, client, workspace, git):
    job = client.post("/api/processes/p1/compile", json={}).json()["job"]
    assert finished(ctl, job["id"])["result_commit"]
    assert [j["id"] for j in client.get("/api/jobs", params={"active": "true"}).json()["jobs"]] == [job["id"]]

    first = client.post(f"/api/jobs/{job['id']}/integrate")
    assert first.status_code == 200
    integration = first.json()["integration"]
    assert (integration["mode"], integration["target"]) == ("fast_forward", "main")
    assert git(workspace, "rev-parse", "HEAD").strip() == integration["head"]
    assert (workspace / "processes/p1/NOTES.md").exists()
    assert client.post(f"/api/jobs/{job['id']}/integrate").json()["integration"] == integration
    assert client.get("/api/jobs", params={"active": "true"}).json() == {"jobs": []}


def test_answers_resubmit_the_compile_job(make_job_controller, compile_view):
    ctl = make_job_controller(compile=f"{H}:ask_then_finish")
    client = api_client(ctl)
    job = client.post("/api/processes/p1/compile", json={}).json()["job"]
    assert finished(ctl, job["id"])["status"] == "awaiting_input"

    response = client.post(f"/api/jobs/{job['id']}/answers",
                           json={"question_id": "read.example1", "decision": "confirm"})
    assert response.status_code == 200 and response.json()["id"] == job["id"]
    assert compile_view == [[DecisionAnswer(question_id="read.example1", decision="confirm")]]
    record = settle(ctl.ctx.runner, job["id"])
    assert (record.status, record.attempt, record.inputs["answers"]) == ("succeeded", 2, {"read.example1": "accept"})


def test_a_text_answer_and_a_malformed_answer(make_job_controller, compile_view):
    ctl = make_job_controller(compile=f"{H}:ask_then_finish")
    client = api_client(ctl)
    job = client.post("/api/processes/p1/compile", json={}).json()["job"]
    finished(ctl, job["id"])
    bad = client.post(f"/api/jobs/{job['id']}/answers", json={"question_id": "q", "decision": "maybe"})
    assert (bad.status_code, bad.json()["error"]["code"]) == (422, "invalid")
    assert client.post(f"/api/jobs/{job['id']}/answers",
                       json={"question_id": "read.example1", "text": "only when empty"}).status_code == 200
    assert compile_view == [[TextAnswer(question_id="read.example1", text="only when empty")]]


def test_cancel_a_job_awaiting_input_and_refuse_a_finished_one(make_job_controller, compile_view):
    ctl = make_job_controller(compile=f"{H}:ask_then_finish")
    client = api_client(ctl)
    job = client.post("/api/processes/p1/compile", json={}).json()["job"]
    finished(ctl, job["id"])
    response = client.post(f"/api/jobs/{job['id']}/cancel")
    assert (response.status_code, response.json()["status"]) == (200, "cancelled")
    again = client.post(f"/api/jobs/{job['id']}/answers", json={"question_id": "q", "text": "x"})
    assert (again.status_code, again.json()["error"]["code"]) == (409, "job_state")

    bake = client.post("/api/processes/p1/bake").json()
    finished(ctl, bake["id"])
    refused = client.post(f"/api/jobs/{bake['id']}/cancel")
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "job_state")


# --- the job event stream ---------------------------------------------------------------------------------------------

def test_the_event_stream_sends_the_log_the_status_and_ends_with_the_job(ctl, client):
    job = client.post("/api/processes/p1/build", json={}).json()["job"]
    record = finished(ctl, job["id"])
    response = client.get(f"/api/jobs/{job['id']}/events")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert response.text.startswith("retry: 2000\n\n")
    frames = parse_sse(response.text)

    assert [event for _, event, _ in frames] == ["log", "status", "end"]
    log = frames[0][2]
    assert f"hello from {job['id']}" in log["text"] and frames[0][0] == str(log["offset"])
    assert frames[1][2] == {"status": "succeeded"}
    assert frames[2][2]["job"]["id"] == job["id"] and frames[2][2]["job"]["status"] == record["status"]

    resumed = parse_sse(client.get(f"/api/jobs/{job['id']}/events", params={"offset": log["offset"]}).text)
    assert [event for _, event, _ in resumed] == ["status", "end"]
    by_header = parse_sse(client.get(f"/api/jobs/{job['id']}/events", headers={"Last-Event-ID": str(log["offset"])})
                          .text)
    assert [event for _, event, _ in by_header] == ["status", "end"]


def test_the_event_stream_ends_when_the_job_awaits_input(make_job_controller, compile_view):
    ctl = make_job_controller(compile=f"{H}:ask_then_finish")
    client = api_client(ctl)
    job = client.post("/api/processes/p1/compile", json={}).json()["job"]
    finished(ctl, job["id"])
    frames = parse_sse(client.get(f"/api/jobs/{job['id']}/events").text)
    assert [(event, data.get("status")) for _, event, data in frames if event != "log"] == [
        ("status", "awaiting_input"), ("end", None)]
    assert frames[-1][2]["job"]["session"] == {"state": "awaiting_input", "steps": [], "questions": [],
                                               "inferred_schemas": {}, "events": []}

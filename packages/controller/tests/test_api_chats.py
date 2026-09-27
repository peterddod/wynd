"""Chat routes (PLAN §3.21; `$DRAFTS/07 §12.1` routes 21-28, §12.4 chat SSE) with a scripted chat provider. The event
stream never ends by itself, so it is read from a real uvicorn server (`LiveServer`)."""

from __future__ import annotations

import threading
import time
import uuid

import pytest

from support.ctl_api_client import LiveServer, ScriptedAgent, api_client, read_frames
from wynd.controller.api import create_app
from wynd.runtime.providers import register_for_tests


@pytest.fixture
def agent():
    fake = ScriptedAgent()
    undo = register_for_tests(fake.name, fake)
    yield fake
    undo()


@pytest.fixture
def ctl(workspace, make_controller, agent):
    ctl = make_controller(workspace, env={"WYND_CHAT_PROVIDER": agent.name})
    yield ctl
    ctl.chats.close()


@pytest.fixture
def client(ctl):
    return api_client(ctl)


def message(text: str, acting_on: str | None = None, client_id: str | None = None) -> dict:
    return {"text": text, "acting_on": acting_on, "client_id": client_id or uuid.uuid4().hex}


def wait_idle(client, chat_id: str, timeout: float = 10) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        snapshot = client.get(f"/api/chats/{chat_id}").json()
        if snapshot["chat"]["running_turn"] is None:
            return snapshot
        assert time.monotonic() < deadline, "the turn never ended"
        time.sleep(0.02)


# --- CRUD -------------------------------------------------------------------------------------------------------------

def test_create_list_rename_and_delete(client):
    first = client.post("/api/chats")
    assert first.status_code == 201
    assert (first.json()["title"], first.json()["job"], first.json()["running_turn"]) == ("New chat", None, None)
    second = client.post("/api/chats", json={"title": "Invoices"}).json()
    assert second["title"] == "Invoices"
    assert {c["id"] for c in client.get("/api/chats").json()["chats"]} == {first.json()["id"], second["id"]}

    renamed = client.patch(f"/api/chats/{second['id']}", json={"title": "Supplier invoices"})
    assert (renamed.status_code, renamed.json()["title"]) == (200, "Supplier invoices")
    empty = client.patch(f"/api/chats/{second['id']}", json={"title": "  "})
    assert (empty.status_code, empty.json()["error"]["code"]) == (422, "invalid")

    snapshot = client.get(f"/api/chats/{second['id']}").json()
    assert (snapshot["chat"]["title"], snapshot["items"], snapshot["cursor"]) == ("Supplier invoices", [], 0)

    deleted = client.delete(f"/api/chats/{second['id']}")
    assert (deleted.status_code, deleted.content) == (204, b"")
    assert client.get(f"/api/chats/{second['id']}").status_code == 404
    assert [c["id"] for c in client.get("/api/chats").json()["chats"]] == [first.json()["id"]]


def test_an_unknown_chat_is_404(client):
    for method, path, body in (("get", "/api/chats/chat_nope", None), ("patch", "/api/chats/chat_nope", {"title": "x"}),
                               ("delete", "/api/chats/chat_nope", None),
                               ("post", "/api/chats/chat_nope/messages", message("hi")),
                               ("post", "/api/chats/chat_nope/cancel", None),
                               ("get", "/api/chats/chat_nope/events", None)):
        response = client.request(method.upper(), path, json=body)
        assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found"), path


# --- turns ------------------------------------------------------------------------------------------------------------

def test_send_a_message_and_read_the_reply(client, agent):
    agent.replies.append("Hello there.")
    chat = client.post("/api/chats").json()
    response = client.post(f"/api/chats/{chat['id']}/messages", json=message("hi"))
    assert response.status_code == 202
    sent = response.json()
    assert sent["turn_id"].startswith("turn_")
    assert (sent["item"]["type"], sent["item"]["text"], sent["item"]["acting_on"]) == ("user", "hi", None)

    snapshot = wait_idle(client, chat["id"])
    assert [(i["type"], i.get("text"), i.get("status")) for i in snapshot["items"]] == [
        ("user", "hi", None), ("assistant", "Hello there.", "done")]
    assert snapshot["chat"]["title"] == "hi" and snapshot["cursor"] > 0


def test_a_repeated_client_id_returns_the_same_turn(client):
    chat = client.post("/api/chats").json()
    body = message("hi", client_id="c-1")
    first = client.post(f"/api/chats/{chat['id']}/messages", json=body).json()
    wait_idle(client, chat["id"])
    assert client.post(f"/api/chats/{chat['id']}/messages", json=body).json() == first


def test_a_second_message_while_a_turn_runs_is_409_and_cancel_stops_it(client, agent):
    agent.wait = threading.Event()
    chat = client.post("/api/chats").json()
    turn = client.post(f"/api/chats/{chat['id']}/messages", json=message("first")).json()["turn_id"]
    busy = client.post(f"/api/chats/{chat['id']}/messages", json=message("second"))
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "turn_in_progress"
    assert client.get(f"/api/chats/{chat['id']}").json()["chat"]["running_turn"] == turn

    assert client.post(f"/api/chats/{chat['id']}/cancel").json() == {"ok": True}
    agent.wait.set()
    snapshot = wait_idle(client, chat["id"])
    assert snapshot["items"][-1]["status"] == "cancelled"


def test_a_turn_acting_on_a_process_locks_its_design(client, agent):
    agent.wait = threading.Event()
    chat = client.post("/api/chats").json()
    turn = client.post(f"/api/chats/{chat['id']}/messages", json=message("rename a step", acting_on="p1")).json()
    design = client.get("/api/processes/p1/design").json()
    assert design["locked_by"] == {"chat_id": chat["id"], "turn_id": turn["turn_id"]}
    body = {"writes": [{"path": design["process_file"]["path"], "base_revision": design["process_file"]["revision"],
                        "doc": design["process_file"]["doc"]}]}
    assert client.post("/api/processes/p1/design", json=body).status_code == 423
    agent.wait.set()
    wait_idle(client, chat["id"])
    assert client.get("/api/processes/p1/design").json()["locked_by"] is None
    assert client.post(f"/api/chats/{chat['id']}/messages", json=message("hi", acting_on="nope")).status_code == 404


# --- the event stream (07 §12.4) --------------------------------------------------------------------------------------

def test_the_event_stream_carries_a_whole_turn(ctl, agent):
    agent.replies.append("two words")
    chat = ctl.chats.create()
    with LiveServer(create_app(ctl, scheduler=False)) as server:
        def send() -> None:
            api = api_client(ctl)
            api.post(f"/api/chats/{chat.id}/messages", json=message("hi"))

        text, frames = read_frames(f"{server.url}/api/chats/{chat.id}/events?since=0",
                                   lambda fs: any(e == "turn" and d["status"] == "done" for _, e, d in fs),
                                   on_open=send)

    assert text.startswith("retry: 2000\n\n")
    ids = [int(frame_id) for frame_id, _, _ in frames]
    assert ids == list(range(1, len(frames) + 1))
    assert [event for _, event, _ in frames] == ["item", "turn", "item", "delta", "delta", "item", "turn"]
    assert frames[0][2]["item"]["type"] == "user" and frames[1][2]["status"] == "running"
    assert frames[2][2]["item"]["status"] == "streaming"
    deltas = [data for _, event, data in frames if event == "delta"]
    assert [d["item_id"] for d in deltas] == [frames[2][2]["item"]["id"]] * 2
    assert "two" in deltas[0]["text"] and "words" in deltas[1]["text"]
    assert frames[5][2]["item"]["text"] == "two words" and frames[5][2]["item"]["status"] == "done"
    assert frames[6][2] == {"turn_id": frames[1][2]["turn_id"], "status": "done", "acting_on": None, "edited": [],
                            "error": None}


def test_the_event_stream_resumes_or_resets(ctl, client):
    chat = client.post("/api/chats").json()
    client.post(f"/api/chats/{chat['id']}/messages", json=message("hi"))
    cursor = wait_idle(client, chat["id"])["cursor"]
    with LiveServer(create_app(ctl, scheduler=False)) as server:
        url = f"{server.url}/api/chats/{chat['id']}/events"
        _, resumed = read_frames(f"{url}?since={cursor - 1}", lambda fs: len(fs) >= 1)
        _, by_header = read_frames(f"{url}?since=0", lambda fs: len(fs) >= 1,
                                   headers={"Last-Event-ID": str(cursor - 1)})
        _, reset = read_frames(f"{url}?since={cursor + 50}", lambda fs: len(fs) >= 1)

    assert resumed == by_header and [(i, e) for i, e, _ in resumed] == [(str(cursor), "turn")]
    assert reset == [(str(cursor), "reset", {})]


def test_the_event_stream_accepts_the_access_token(ctl):
    chat = ctl.chats.create()
    with LiveServer(create_app(ctl, scheduler=False, api_token="s3cret")) as server:
        url = f"{server.url}/api/chats/{chat.id}/events"
        import httpx

        assert httpx.get(url).status_code == 401
        text, frames = read_frames(f"{url}?since=0&access_token=s3cret", lambda fs: True)
    assert text.startswith("retry: 2000") and frames == []

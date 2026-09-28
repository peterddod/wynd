"""Chat routes: list, create, snapshot, rename, delete, messages, cancel, events SSE (PLAN §3.21; `$DRAFTS/06 §8.3`
routes 21-28, `$DRAFTS/07 §12.4`).

The event stream sends the chat's `item`/`delta`/`turn` events after `since` (ids are the per-chat event counter) and
never ends by itself. A cursor the controller's log cannot resume from gets `reset {}` with the current cursor as its
id; the client refetches the snapshot and reconnects from the snapshot's cursor.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from wynd.controller.api.app import Ctl
from wynd.controller.api.models_web import CreateChatRequest, RenameChatRequest, SendMessageRequest, SendMessageResult
from wynd.controller.api.sse import Frame, event_stream, resume_cursor
from wynd.controller.controller import Controller

router = APIRouter()


@router.get("/api/chats")
def list_chats(ctl: Ctl):
    return {"chats": ctl.chats.list()}


@router.post("/api/chats", status_code=201)
def create_chat(ctl: Ctl, body: CreateChatRequest | None = None):
    return ctl.chats.create(None if body is None else body.title)


@router.get("/api/chats/{chat_id}")
def get_chat(chat_id: str, ctl: Ctl):
    return ctl.chats.snapshot(chat_id)


@router.patch("/api/chats/{chat_id}")
def rename_chat(chat_id: str, body: RenameChatRequest, ctl: Ctl):
    return ctl.chats.rename(chat_id, body.title)


@router.delete("/api/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: str, ctl: Ctl):
    ctl.chats.delete(chat_id)
    return Response(status_code=204)


@router.post("/api/chats/{chat_id}/messages", status_code=202)
def send_message(chat_id: str, body: SendMessageRequest, ctl: Ctl):
    turn_id, item = ctl.chats.send(chat_id, body)
    return SendMessageResult(turn_id=turn_id, item=item)


@router.post("/api/chats/{chat_id}/cancel")
def cancel_turn(chat_id: str, ctl: Ctl):
    ctl.chats.cancel(chat_id)
    return {"ok": True}


@router.get("/api/chats/{chat_id}/events")
async def chat_events(chat_id: str, request: Request, ctl: Ctl, since: int = 0):
    await run_in_threadpool(ctl.chats.snapshot, chat_id)
    return event_stream(request, _chat_frames(ctl, chat_id), resume_cursor(request, since))


def _chat_frames(ctl: Controller, chat_id: str):
    def fetch(since: int) -> tuple[list[Frame], bool]:
        events, reset = ctl.chats.events(chat_id, since)
        if reset:
            return [(ctl.chats.snapshot(chat_id).cursor, "reset", {})], False
        return [(event.id, event.event, event.data) for event in events], False

    return fetch

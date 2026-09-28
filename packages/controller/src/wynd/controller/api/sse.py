"""Server-sent events (PLAN §3.21; `$DRAFTS/06 §8.2`).

Headers `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`; first line
`retry: 2000`; frames `id: <n>\\nevent: <type>\\ndata: <single-line json>\\n\\n`; `: ping` every 15 s. Every stream
polls its store (no pub/sub): `fetch(cursor) -> (frames, done)` runs in the threadpool every 0.25 s, the cursor is the
id of the last frame, and the stream ends after the frames of a `done` poll (the fetch supplies its own `end` frame)
or when the client disconnects. A controller error while polling (e.g. the chat was deleted) ends the stream with
`end {"error": <code>, "message"}`. Resume: the `Last-Event-ID` header wins over the `since`/`offset` query value.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any

from pydantic_core import to_json
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import StreamingResponse

from wynd.controller.errors import WyndError

HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
RETRY = "retry: 2000\n\n"
PING = ": ping\n\n"
PING_S = 15.0
POLL_S = 0.25

Frame = tuple[int, str, dict]


def frames(items: Iterable[Frame]) -> str:
    """Render `(id, event, data)` items as SSE frames."""
    return "".join(f"id: {id_}\nevent: {event}\ndata: {to_json(data).decode()}\n\n" for id_, event, data in items)


async def poll_stream(
    request: Any,
    fetch: Callable[[int], tuple[list[Frame], bool]],
    since: int,
) -> AsyncIterator[str]:
    """Poll `fetch(cursor)` (in the threadpool) until it reports done or the client disconnects."""
    yield RETRY
    cursor = since
    quiet_since = time.monotonic()
    while True:
        try:
            items, done = await run_in_threadpool(fetch, cursor)
        except WyndError as err:
            yield frames([(cursor, "end", {"error": err.code, "message": err.message})])
            return
        if items:
            yield frames(items)
            cursor = items[-1][0]
            quiet_since = time.monotonic()
        if done or await request.is_disconnected():
            return
        if time.monotonic() - quiet_since >= PING_S:
            yield PING
            quiet_since = time.monotonic()
        await asyncio.sleep(POLL_S)


def resume_cursor(request: Request, since: int) -> int:
    """The `Last-Event-ID` a reconnecting `EventSource` sends, else the query value."""
    last = request.headers.get("last-event-id", "").strip()
    return int(last) if last.isdigit() else since


def event_stream(request: Request, fetch: Callable[[int], tuple[list[Frame], bool]], since: int) -> StreamingResponse:
    return StreamingResponse(poll_stream(request, fetch, since), media_type="text/event-stream", headers=HEADERS)

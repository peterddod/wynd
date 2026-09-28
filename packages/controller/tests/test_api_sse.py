"""Server-sent events (PLAN §3.21; `$DRAFTS/06 §8.2`): framing, the polling loop, pings, resume."""

from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request

from support.ctl_api_client import parse_sse
from wynd.controller.api import sse
from wynd.controller.errors import NotFound


class FakeRequest:
    """`is_disconnected()` turns true after `polls` checks."""

    def __init__(self, polls: int = 1000) -> None:
        self.polls = polls
        self.checks = 0

    async def is_disconnected(self) -> bool:
        self.checks += 1
        return self.checks > self.polls


def collect(request, fetch, since: int = 0) -> list[str]:
    async def run() -> list[str]:
        return [chunk async for chunk in sse.poll_stream(request, fetch, since)]

    return asyncio.run(run())


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch):
    monkeypatch.setattr(sse, "POLL_S", 0.001)


def test_frames_are_id_event_and_one_line_of_compact_json():
    text = sse.frames([(3, "trace", {"seq": 3, "text": "two\nlines", "name": "café"}), (4, "end", {})])
    assert text == ('id: 3\nevent: trace\ndata: {"seq":3,"text":"two\\nlines","name":"café"}\n\n'
                    "id: 4\nevent: end\ndata: {}\n\n")


def test_the_stream_starts_with_retry_and_passes_the_cursor_to_each_poll():
    cursors = []
    polls = iter([([(1, "trace", {"seq": 1}), (2, "trace", {"seq": 2})], False), ([], False),
                  ([(3, "trace", {"seq": 3}), (3, "end", {})], True)])

    def fetch(cursor):
        cursors.append(cursor)
        return next(polls)

    chunks = collect(FakeRequest(), fetch, since=0)
    assert chunks[0] == "retry: 2000\n\n"
    assert cursors == [0, 2, 2]
    assert parse_sse("".join(chunks)) == [("1", "trace", {"seq": 1}), ("2", "trace", {"seq": 2}),
                                          ("3", "trace", {"seq": 3}), ("3", "end", {})]


def test_the_stream_stops_when_the_client_disconnects():
    calls = []

    def fetch(cursor):
        calls.append(cursor)
        return [], False

    chunks = collect(FakeRequest(polls=2), fetch, since=7)
    assert chunks == ["retry: 2000\n\n"] and calls == [7, 7, 7]


def test_a_quiet_stream_pings(monkeypatch):
    monkeypatch.setattr(sse, "PING_S", 0.0)
    chunks = collect(FakeRequest(polls=2), lambda cursor: ([], False))
    assert chunks == ["retry: 2000\n\n", ": ping\n\n", ": ping\n\n"]


def test_a_controller_error_while_polling_ends_the_stream():
    def fetch(cursor):
        raise NotFound("no chat 'chat_1'")

    chunks = collect(FakeRequest(), fetch, since=5)
    assert parse_sse("".join(chunks)) == [("5", "end", {"error": "not_found", "message": "no chat 'chat_1'"})]


def request_with(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b""})


@pytest.mark.parametrize(("headers", "since", "cursor"), [
    ({}, 4, 4),
    ({"Last-Event-ID": "12"}, 4, 12),
    ({"Last-Event-ID": "not-a-number"}, 4, 4),
])
def test_last_event_id_wins_over_the_query_cursor(headers, since, cursor):
    assert sse.resume_cursor(request_with(headers), since) == cursor


def test_event_stream_sets_the_sse_headers():
    response = sse.event_stream(request_with({}), lambda cursor: ([], True), 0)
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["content-type"].startswith("text/event-stream")

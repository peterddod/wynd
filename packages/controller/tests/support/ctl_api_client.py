"""CTL-API test helpers.

- `api_client(ctl, **kw)`: a `TestClient` over `create_app(ctl, scheduler=False, **kw)` that answers server errors
  with the app's 500 envelope instead of raising them.
- `parse_sse(text) -> [(id, event, data)]`: the frames of a finished SSE body (comments and `retry:` skipped).
- `LiveServer(app)`: the app served by uvicorn on a free 127.0.0.1 port in a thread (`url`), for the streams that
  never end by themselves (a `TestClient` buffers a whole response); `read_frames(url, until, …)` reads frames from
  such a stream until `until(frames)` holds, then disconnects.
- `ScriptedAgent`: a chat `AgentProvider` that streams each reply's text as `text` events and answers
  `{"reply": text}`; `wait` holds a turn until the event is set.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx
import uvicorn
from fastapi.testclient import TestClient

from wynd.controller.api import create_app
from wynd.runtime.providers.types import AgentResponse
from wynd.runtime.usage import Usage
from wynd.spec.fragments import EnvFragment

Frame = tuple[str | None, str, Any]


def api_client(ctl, **kw) -> TestClient:
    kw.setdefault("scheduler", False)
    return TestClient(create_app(ctl, **kw), raise_server_exceptions=False)


def parse_sse(text: str) -> list[Frame]:
    frames = []
    for block in text.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if line.startswith(":") or line.startswith("retry:"):
                continue
            key, _, value = line.partition(": ")
            fields[key] = value
        if "event" in fields:
            frames.append((fields.get("id"), fields["event"], json.loads(fields["data"])))
    return frames


class LiveServer:
    def __init__(self, app) -> None:
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning",
                                                    lifespan="off"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.url = ""

    def __enter__(self) -> LiveServer:
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            assert time.monotonic() < deadline, "uvicorn did not start"
            time.sleep(0.02)
        port = self.server.servers[0].sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.should_exit = True
        self.thread.join(10)


def read_frames(url: str, until: Callable[[list[Frame]], bool], *, headers: dict[str, str] | None = None,
                on_open: Callable[[], None] | None = None, timeout: float = 10.0) -> tuple[str, list[Frame]]:
    """-> (the raw text read, its frames); disconnects once `until(frames)` holds."""
    text = ""
    with httpx.Client(timeout=timeout) as client, client.stream("GET", url, headers=headers) as response:
        assert response.status_code == 200, response.read()
        if on_open is not None:
            on_open()
        deadline = time.monotonic() + timeout
        for chunk in response.iter_text():
            text += chunk
            frames = parse_sse(text)
            if until(frames):
                return text, frames
            assert time.monotonic() < deadline, f"stream never satisfied the condition: {text!r}"
    raise AssertionError(f"the stream ended early: {text!r}")


class ScriptedAgent:
    name = "api-chat-fake"
    kind = "agent"
    default_tiers = {"cheap": "fake-cheap", "standard": "fake-standard", "strong": "fake-strong"}
    env_fragment = EnvFragment()

    def __init__(self) -> None:
        self.replies: list[str] = []
        self.wait: threading.Event | None = None

    def __call__(self, tiers=None) -> ScriptedAgent:
        return self

    def tiers(self) -> dict[str, str]:
        return dict(self.default_tiers)

    def run(self, req):
        text = self.replies.pop(0) if self.replies else "Done."
        for word in text.split(" "):
            req.on_event({"type": "text", "text": word + " "})
        if self.wait is not None:
            assert self.wait.wait(10)
        return AgentResponse(structured_output={"reply": text}, model_id=req.model_id, transcript=[], session={},
                             usage=Usage(input_tokens=10, output_tokens=2, calls=1))

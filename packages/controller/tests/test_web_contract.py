"""The web contract (PLAN §3.21 amendment 15; `$DRAFTS/07 §12.2`, P-FIXTURES): every fixture the web tests against is
exactly what the controller would serialise.

`packages/web/src/api/fixtures/index.json` maps each JSON fixture to its model (`Name` or `Name[]`, looked up in
`wynd.controller.api.models_web`, then `wynd.controller.models`). A fixture must validate against the model and dump
back to itself unchanged, so it carries every field under the controller's names and nothing else. A `Job.session`
is a `CompileSession`; trace fixtures are PLAN §3.13 events; `chat.events.txt` is a chat SSE body (§12.4).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

from support.ctl_api_client import parse_sse
from wynd.controller import models
from wynd.controller.api import models_web
from wynd.runtime.trace import EVENT_TYPES, parse_event

FIXTURES = Path(__file__).resolve().parents[2] / "web" / "src" / "api" / "fixtures"
INDEX: dict[str, str] = json.loads((FIXTURES / "index.json").read_text())


def adapter(name: str) -> TypeAdapter:
    base = name.removesuffix("[]")
    model = getattr(models_web, base, None) or getattr(models, base)
    return TypeAdapter(list[model] if name.endswith("[]") else model)


def load(fixture: str) -> Any:
    return json.loads((FIXTURES / fixture).read_text())


def test_the_index_covers_every_json_fixture():
    assert sorted(INDEX) == sorted(p.name for p in FIXTURES.glob("*.json") if p.name != "index.json")


@pytest.mark.parametrize("fixture", sorted(INDEX))
def test_the_fixture_is_what_the_controller_serialises(fixture):
    model = adapter(INDEX[fixture])
    value = model.validate_json((FIXTURES / fixture).read_text())
    assert model.dump_python(value, mode="json", by_alias=True) == load(fixture)


@pytest.mark.parametrize("fixture", sorted(f for f, name in INDEX.items() if name in ("Job", "JobWithChat")))
def test_a_job_session_is_a_compile_session(fixture):
    data = load(fixture)
    job = data.get("job", data)
    if job["session"] is None:
        return
    session = models_web.CompileSession.model_validate(job["session"])
    # questions may omit the controller additions `detail`/`default` (types.ts: "may be absent")
    assert session.model_dump(mode="json", by_alias=True, exclude_unset=True) == job["session"]


@pytest.mark.parametrize("fixture", sorted(f for f, name in INDEX.items() if name == "TraceEvent[]"))
def test_trace_fixtures_are_plan_events(fixture):
    events = load(fixture)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert {e["v"] for e in events} == {1} and len({e["run_id"] for e in events}) == 1
    assert events[0]["type"] == "run.start" and events[-1]["type"] == "run.end"
    for event in events:
        assert event["type"] in EVENT_TYPES
        parse_event(event)                                  # the type's own fields are present
        assert "path" not in event
        assert "mode" not in event or event["type"] == "run.start"


def test_the_chat_event_stream_fixture_is_a_chat_sse_body():
    text = (FIXTURES / "chat.events.txt").read_text()
    assert text.startswith("retry: 2000\n\n")
    frames = parse_sse(text)
    ids = [int(frame_id) for frame_id, _, _ in frames]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    item = TypeAdapter(models_web.ChatItem)
    for _, event, data in frames:
        match event:
            case "item":
                assert set(data) == {"item"}
                assert item.dump_python(item.validate_python(data["item"]), mode="json") == data["item"]
            case "delta":
                assert set(data) == {"item_id", "text"}
            case "turn":
                assert set(data) == {"turn_id", "status", "acting_on", "edited", "error"}
                assert data["status"] in ("running", "done", "error", "cancelled")
            case _:
                pytest.fail(f"unexpected chat event {event!r}")

"""`RunService` with the local target (PLAN §8.1 runs/service row, §3.14 run metadata, §15 item 68 env gate): real
`run_local` with venv workers on the `ws_basic` fixture (offline)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wynd.controller.errors import Conflict, EnvMissing, Invalid, NotBuilt, NotFound, Unavailable
from wynd.controller.models import CreateRunRequest, ImageTarget, LocalTarget, ReleaseTarget, Run
from wynd.controller.runs.tree import render_events


@pytest.fixture
def records_dir(tmp_path) -> Path:
    return tmp_path / "records"


@pytest.fixture
def controller(workspace, make_controller, records_dir):
    return make_controller(workspace, env={"RECORDS_DIR": str(records_dir)})


def run_records(controller) -> list[dict]:
    return controller.ctx.stores.runs.list(kind="run")


def test_a_local_run_with_records_dir_unset_raises_env_missing_and_creates_no_record(workspace, make_controller):
    controller = make_controller(workspace, env={"RECORDS_DIR": None})
    with pytest.raises(EnvMissing) as info:
        controller.runs.run("p1", {"text": "hello world"})
    err = info.value
    assert (err.code, err.http, err.exit) == ("env_missing", 412, 3)
    assert err.details["missing"] == ["RECORDS_DIR"] and "RECORDS_DIR" in err.message
    assert run_records(controller) == []
    with pytest.raises(EnvMissing):
        controller.runs.start(CreateRunRequest(process_id="p1", inputs={"text": "hello world"}))
    assert run_records(controller) == []


def test_a_local_run_end_to_end(controller, records_dir):
    events: list[dict] = []
    run = controller.runs.run("p1", {"text": "hello world"}, on_event=events.append, trigger="manual")

    assert (run.process_id, run.mode, run.target, run.trigger) == ("p1", "local", LocalTarget(), "manual")
    assert (run.status, run.exit, run.error) == ("succeeded", "done", None)
    assert run.outputs == {"words": 2, "path": str(records_dir / "count.txt")}
    assert (records_dir / "count.txt").read_text() == "HELLO WORLD"            # written by the count worker
    assert run.started_at is not None and run.finished_at is not None and run.duration_ms > 0
    assert run.inputs == {"text": "hello world"}

    record = controller.ctx.stores.runs.get(run.id)
    assert record["meta"] == {"trigger": "manual", "release_id": None, "target": {"kind": "local"}}
    assert controller.runs.get(run.id) == run
    assert [r.id for r in controller.runs.list(process_id="p1")] == [run.id]
    assert controller.runs.list(process_id="p2") == []

    stored = controller.runs.events(run.id)
    assert stored == events                                                    # passed through unchanged
    assert [e["type"] for e in stored][0] == "run.start" and stored[-1]["type"] == "run.end"
    assert [(e["step"], e["exit"]) for e in stored if e["type"] == "step.end"] == [("upper", "done"),
                                                                                 ("count", "done")]
    assert controller.runs.events(run.id, since=stored[-2]["seq"]) == stored[-1:]
    text = render_events(stored)
    assert text.splitlines()[0].startswith(f"run {run.id}  p1  local  exit=done")
    assert "├─ upper" in text and "└─ count" in text


def test_a_declared_non_error_exit_and_a_step_error(controller, records_dir):
    empty = controller.runs.run("p1", {"text": "   "})
    assert (empty.status, empty.exit, empty.outputs) == ("succeeded", "empty", {})

    records_dir.parent.mkdir(parents=True, exist_ok=True)
    records_dir.write_text("a file where the directory should be")
    failed = controller.runs.run("p1", {"text": "hello"})
    assert (failed.status, failed.exit) == ("failed", "error")
    assert (failed.error.cause, failed.error.step) == ("step_error", "count")
    assert failed.outputs == {"error": failed.error.model_dump(mode="json")}


def test_refusals_create_no_record(controller):
    with pytest.raises(Invalid, match="invalid process inputs"):
        controller.runs.run("p1", {"words": 1})
    with pytest.raises(Conflict, match="design phase") as info:
        controller.runs.run("p2", {"name": "Ada"})
    assert info.value.details == {"steps": ["p2#tag"]}
    with pytest.raises(NotFound):
        controller.runs.run("nope", {})
    with pytest.raises(NotBuilt):
        controller.runs.run("p1", {"text": "x"}, target=ImageTarget())
    assert run_records(controller) == []


def test_start_runs_in_the_background_and_follow_streams_every_event(controller):
    run = controller.runs.start(CreateRunRequest(process_id="p1", inputs={"text": "one two three"}))
    assert (run.status, run.mode, run.trigger) == ("running", "local", "api")
    assert controller.runs.get(run.id).status in ("running", "succeeded")
    assert run.id in [r.id for r in controller.runs.list()]

    seen: list[dict] = []
    final = controller.runs.follow(run.id, on_event=seen.append, poll=0.05, timeout=120)
    assert (final.id, final.status, final.outputs["words"]) == (run.id, "succeeded", 3)
    assert [e["seq"] for e in seen] == list(range(1, len(seen) + 1))
    assert seen[-1]["type"] == "run.end"
    assert controller.runs.follow(run.id, since=len(seen), on_event=seen.append) == final     # nothing new


def test_start_refuses_bad_inputs_synchronously(controller):
    with pytest.raises(Invalid, match="text"):
        controller.runs.start(CreateRunRequest(process_id="p1", inputs={}))
    with pytest.raises(NotFound):
        controller.runs.start(CreateRunRequest(process_id="nope", inputs={}))
    assert run_records(controller) == []


def test_a_background_run_that_fails_to_start_is_recorded_failed(controller):
    run = controller.runs.start(CreateRunRequest(process_id="p2", inputs={"name": "Ada"}))
    final = controller.runs.follow(run.id, on_event=lambda e: None, poll=0.05, timeout=60)
    assert (final.status, final.exit) == ("failed", "error")
    assert final.error.cause == "internal" and "design phase" in final.error.message
    assert controller.ctx.stores.runs.get(run.id)["meta"]["target"] == {"kind": "local"}
    assert controller.runs.events(run.id) == []


def test_follow_times_out_while_a_run_is_still_running(controller):
    controller.ctx.stores.runs.create({"id": "run_stuck", "kind": "run", "process": "p1", "status": "running"})
    with pytest.raises(Unavailable, match="still running"):
        controller.runs.follow("run_stuck", on_event=lambda e: None, poll=0.01, timeout=0.05)
    with pytest.raises(NotFound):
        controller.runs.get("run_unknown")
    with pytest.raises(NotFound):
        controller.runs.events("run_unknown")


def test_run_projection_of_image_and_release_records():
    base = {"id": "run_1", "kind": "run", "process": "p1", "status": "succeeded", "mode": "image", "ref": "abc"}
    image = Run.from_record({**base, "meta": {"trigger": "api", "release_id": None,
                                              "target": {"kind": "image", "commit": "abc"}}})
    assert (image.target, image.commit, image.mode, image.trigger) == (ImageTarget(commit="abc"), "abc", "image", "api")
    release = Run.from_record({**base, "meta": {"trigger": "schedule", "release_id": "rel_1",
                                                "target": {"kind": "release", "commit": "abc"}}})
    assert (release.target, release.release_id, release.trigger) == (ReleaseTarget(release_id="rel_1"), "rel_1",
                                                                      "schedule")
    legacy = Run.from_record({"id": "run_2", "kind": "run", "process": "p1", "status": "queued",
                              "meta": {"trigger": "cron"}})
    assert (legacy.target, legacy.mode, legacy.trigger) == (LocalTarget(), "local", "api")

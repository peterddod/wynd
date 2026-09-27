"""Image runs through the run API and their mirror into the controller's stores (CTL-M2; PLAN §8.1, §3.21 amendment
7, `$DRAFTS/06 §5.10`)."""

from __future__ import annotations

import base64
import hashlib
from datetime import UTC, datetime

import pytest

from support.ctl_m2_runapi import FakeDocker, FakeRunApi, trace_events
from wynd.controller.errors import Invalid, NotBuilt
from wynd.controller.models import ImageTarget
from wynd.controller.runs.image import path_inputs_to_files, run_image
from wynd.controller.runs.mirror import mirror_run
from wynd.controller.serving import ServeService
from wynd.controller.status import process_head
from wynd.process.artefacts import BuildInfo
from wynd.runtime.supervisor.client import RunApiError
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.fragments import EnvVar

IMAGE = "wynd/p1:0123456789ab"
P1_WITH_PATHS = """\
kind: process
name: p1
provider: fake
env:
  vars:
    RECORDS_DIR: Directory where the upper-cased text is saved.
entry: upper
inputs:
  text: string
  doc: path
  extra: path?
outputs:
  done:
    words: integer
    path: string
  empty: {}
steps:
  upper: { use: ./steps/upper }
  count: { use: ./steps/count }
edges:
  - from: upper.done
    to: count
    with: { text: steps.upper.outputs.text, dest: env.RECORDS_DIR }
  - from: upper.empty
    to: $exit.empty
  - from: count.done
    to: $exit.done
    with: { words: steps.count.outputs.words, path: steps.count.outputs.path }
"""


def sha8(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:8]


@pytest.fixture
def api() -> FakeRunApi:
    return FakeRunApi()


@pytest.fixture
def fake_docker(api, monkeypatch) -> FakeDocker:
    manifest = EnvManifest(process="p1", vars=[EnvVar(name="RECORDS_DIR")])
    labels = {"dev.wynd.process": "p1", "dev.wynd.commit": "0123456789ab", "dev.wynd.run-api-port": "8080",
              "dev.wynd.env-manifest": manifest.model_dump_json()}
    return FakeDocker(api, {IMAGE: labels}).install(monkeypatch)


@pytest.fixture
def ws(make_workspace):
    return make_workspace(files={"processes/p1/process.yaml": P1_WITH_PATHS, "docs/note.txt": "a workspace file\n"})


@pytest.fixture
def ctl(ws, make_controller, api):
    ctl = make_controller(ws, env={"RECORDS_DIR": "/data/records", "WYND_RUN_API_TOKEN": "tok-9"})
    ctl.serve = ServeService(ctl.ctx, ctl, run_api_client=api)
    return ctl


@pytest.fixture
def head(ctl) -> str:
    return process_head(ctl.ctx, "p1")[0]


@pytest.fixture
def built(ctl, head, tmp_path) -> BuildInfo:
    staged = tmp_path / "staged-build"
    staged.mkdir()
    info = BuildInfo(process="p1", commit=head, source_sha=head, job_id=None, process_hash="sha256:" + "0" * 64,
                     image=IMAGE, image_id=None, image_digest=None, base={}, manifest=EnvManifest(process="p1", vars=[]),
                     created_at=datetime(2026, 9, 27, tzinfo=UTC), dir="")
    return ctl.ctx.artefacts.put_build(staged, info)


def upload(ctl, name: str, data: bytes):
    path = ctl.ctx.state_dir / "uploads" / hashlib.sha256(data).hexdigest() / name
    path.parent.mkdir(parents=True)
    path.write_bytes(data)
    return path


# --- path inputs -> run-API files -------------------------------------------------------------------------------------

def test_an_upload_path_becomes_a_file_reference(ctl):
    data = b"%PDF-1.4 upload"
    path = upload(ctl, "acme invoice#1.pdf", data)

    inputs, files = path_inputs_to_files(ctl, "p1", {"text": "hi", "doc": str(path)})

    name = f"{sha8(data)}-acme_invoice_1.pdf"                  # the run API accepts only id-safe file names
    assert inputs == {"text": "hi", "doc": {"$file": name}}
    assert files == {name: {"content_base64": base64.b64encode(data).decode()}}


def test_a_workspace_file_becomes_a_file_reference(ctl, ws):
    data = (ws / "docs" / "note.txt").read_bytes()
    name = f"{sha8(data)}-note.txt"

    relative, files = path_inputs_to_files(ctl, "p1", {"doc": "docs/note.txt"})
    absolute, again = path_inputs_to_files(ctl, "p1", {"doc": str(ws / "docs" / "note.txt")})

    assert relative == absolute == {"doc": {"$file": name}}
    assert files == again == {name: {"content_base64": base64.b64encode(data).decode()}}


def test_an_optional_path_field_is_a_path_field(ctl, ws):
    inputs, files = path_inputs_to_files(ctl, "p1", {"doc": "missing.pdf", "extra": "docs/note.txt"})
    assert inputs["extra"] == {"$file": f"{sha8((ws / 'docs/note.txt').read_bytes())}-note.txt"}
    assert inputs["doc"] == "missing.pdf" and len(files) == 1


def test_values_that_name_no_file_pass_through(ctl, ws):
    given = {"text": "docs/note.txt", "doc": str(ws / "nope.pdf"), "extra": None}
    inputs, files = path_inputs_to_files(ctl, "p1", given)
    assert inputs == given                                     # a string field naming a file is not a path field
    assert files == {}


def test_a_directory_is_not_a_file(ctl, ws):
    inputs, files = path_inputs_to_files(ctl, "p1", {"doc": "docs"})
    assert inputs == {"doc": "docs"} and files == {}


# --- run_image ----------------------------------------------------------------------------------------------------------

def test_run_image_on_a_warm_container_mirrors_the_run(ctl, built, head, api, fake_docker):
    served = ctl.serve.serve(IMAGE)
    fake_docker.calls.clear()
    data = b"%PDF upload"
    path = upload(ctl, "inv.pdf", data)
    seen: list[dict] = []

    run = run_image(ctl, "p1", {"text": "hi", "doc": str(path)}, on_event=seen.append, trigger="manual",
                    run_api_client=api, run_id="run_fixed_1")

    assert fake_docker.calls == []                             # warm: no Docker call at all
    [submit] = api.submits
    name = f"{sha8(data)}-inv.pdf"
    assert submit["url"] == served.url
    assert submit["run_id"] == "run_fixed_1"
    assert submit["inputs"] == {"text": "hi", "doc": {"$file": name}}
    assert submit["files"] == {name: data}
    metadata = {"trigger": "manual", "release_id": None, "target": {"kind": "image", "commit": head}}
    assert submit["metadata"] == metadata
    assert submit["token"] == "tok-9"

    events = trace_events("run_fixed_1", submit["inputs"], outputs={"words": 2})
    assert seen == events
    assert ctl.ctx.stores.traces.read("run_fixed_1") == events

    record = ctl.ctx.stores.runs.get("run_fixed_1")
    assert record["kind"] == "run" and record["mode"] == "image"
    assert record["meta"] == metadata
    assert (record["status"], record["exit"], record["outputs"]) == ("succeeded", "done", {"words": 2})
    assert record["inputs"] == submit["inputs"]
    assert record["usage"]["calls"] == 1
    assert record["trace"] == ctl.ctx.stores.traces.uri("run_fixed_1")
    assert record["trace_bytes"] == (ctl.ctx.state_dir / "traces" / "run_fixed_1.jsonl").stat().st_size
    assert record["workspace_bytes"] is None

    assert run.id == "run_fixed_1" and run.mode == "image" and run.status == "succeeded"
    assert run.target == ImageTarget(commit=head) and run.trigger == "manual"


def test_run_image_starts_and_stops_an_ephemeral_container(ctl, built, api, fake_docker):
    run = run_image(ctl, "p1", {"text": "hi"}, run_api_client=api)

    [started] = fake_docker.started()
    assert started["image"] == IMAGE and started["name"].startswith("wynd-run-")
    assert fake_docker.removed()[-1] == started["name"]
    assert ctl.serve.ephemeral == {} and ctl.serve.list() == []
    assert run.id.startswith("run_") and api.submits[0]["run_id"] == run.id
    assert api.submits[0]["files"] == {}


def test_run_image_at_an_explicit_commit(ctl, built, head, api, fake_docker, commit, ws):
    commit(ws, "move the process HEAD", {"processes/p1/process.yaml": P1_WITH_PATHS + "goal: Later.\n"})
    with pytest.raises(NotBuilt):                              # the default is the new HEAD, which is not built
        run_image(ctl, "p1", {"text": "hi"}, run_api_client=api)
    run = run_image(ctl, "p1", {"text": "hi"}, commit=head, run_api_client=api)
    assert run.target == ImageTarget(commit=head)


def test_run_image_needs_a_build(ctl, api, fake_docker):
    with pytest.raises(NotBuilt):
        run_image(ctl, "p1", {"text": "hi"}, run_api_client=api)
    with pytest.raises(NotBuilt):
        run_image(ctl, "p1", {"text": "hi"}, commit="f" * 40, run_api_client=api)
    assert fake_docker.started() == []


def test_a_refused_submit_is_invalid_and_the_ephemeral_container_stops(ctl, built, api, fake_docker):
    api.refuse = RunApiError("HTTP 422 invalid_inputs: text is required", status=422, code="invalid_inputs")
    with pytest.raises(Invalid, match="invalid_inputs"):
        run_image(ctl, "p1", {}, run_api_client=api, run_id="run_refused_1")
    assert fake_docker.removed()[-1] == fake_docker.started()[0]["name"]
    assert ctl.ctx.stores.runs.get("run_refused_1") is None


# --- mirror_run ---------------------------------------------------------------------------------------------------------

def submitted(api: FakeRunApi, run_id: str):
    client = api("http://127.0.0.1:1")
    client.submit({"text": "hi"}, run_id=run_id, metadata={"trigger": "api"})
    return client


def test_mirror_resumes_a_dropped_event_stream(ctl, api):
    api.drop_after = 3                                         # status + two trace frames, then the stream ends
    client = submitted(api, "run_drop_1")

    mirror_run(ctl.ctx, client, "run_drop_1", metadata={"trigger": "api"})

    assert api.streams == 2
    assert [e["seq"] for e in ctl.ctx.stores.traces.read("run_drop_1")] == [1, 2, 3, 4]


def test_mirror_gives_up_after_repeated_drops(ctl, api, monkeypatch):
    client = submitted(api, "run_drop_2")
    monkeypatch.setattr(api, "frames", lambda run_id: [])      # every stream ends at once, without `end`
    with pytest.raises(RunApiError, match="lost the event stream"):
        mirror_run(ctl.ctx, client, "run_drop_2", metadata={})


def test_mirror_updates_an_existing_record(ctl, api):
    created = datetime(2026, 9, 27, 11, 0, tzinfo=UTC)
    ctl.ctx.stores.runs.create({"id": "run_known_1", "kind": "run", "process": "p1", "status": "running",
                                "created_at": created.isoformat()})
    client = submitted(api, "run_known_1")

    record = mirror_run(ctl.ctx, client, "run_known_1", metadata={"trigger": "api", "x": 1})

    assert record["status"] == "succeeded" and record["meta"] == {"trigger": "api", "x": 1}
    assert record["created_at"].startswith("2026-09-27T11:00:00")
    assert ctl.ctx.stores.runs.get("run_known_1") == record


def test_mirror_records_a_failed_run(ctl):
    api = FakeRunApi(exit="error", outputs={"error": {"cause": "step_error"}})
    client = submitted(api, "run_failed_1")
    record = mirror_run(ctl.ctx, client, "run_failed_1", metadata={})
    assert (record["status"], record["exit"]) == ("failed", "error")

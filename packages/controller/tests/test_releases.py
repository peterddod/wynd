"""Releases: `ReleaseService` with fake serving/trigger backends and a fake run API, and `DockerServing` with a fake
Docker (CTL-REL; PLAN §8.1 releases row, SPEC §11/§15; `$DRAFTS/06 §5.14`, §11.2 `test_releases.py`)."""

from __future__ import annotations

import json
import logging
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from support.ctl_m2_runapi import FakeDocker, FakeRunApi, FakeRunApiClient
from support.ctl_rel_e2e import fire_manual_and_webhook
from support.ctl_rel_fakes import IMAGE, FakeServing, FakeTriggers, install_backends, put_build
from wynd.controller.api.models_web import (
    CreateReleaseRequest,
    FromEnvBinding,
    ManualTrigger,
    Release,
    ReleasePatch,
    ScheduleTrigger,
    ValueBinding,
    WebhookTrigger,
)
from wynd.controller.errors import (
    Conflict,
    EnvMissing,
    EnvUnbound,
    Invalid,
    NotBuilt,
    NotFound,
    Unauthorized,
    Unavailable,
)
from wynd.controller.models import ReleaseTarget
from wynd.controller.releases.serving import DockerServing, open_serving_backend
from wynd.controller.releases.service import ReleaseService
from wynd.controller.releases.store import RELEASES
from wynd.controller.status import process_head
from wynd.runtime.supervisor.client import RunApiError
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.fragments import EnvGroup, EnvVar

T0 = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)                  # the conftest FakeClock
MANIFEST = EnvManifest(process="p1", vars=[
    EnvVar(name="LOG_LEVEL", required=False, default="info"),
    EnvVar(name="RECORDS_DIR"),
    EnvVar(name="SERVICE_TOKEN", secret=True),
    EnvVar(name="WYND_DATA_DIR", required=False, used_by=["storage"]),
    EnvVar(name="WYND_RUN_API_TOKEN", secret=True, required=False, used_by=["runtime"]),
])
BINDINGS = {"RECORDS_DIR": ValueBinding(value="/data/records"), "SERVICE_TOKEN": FromEnvBinding(from_env="SVC_TOKEN")}
ENV = {"SVC_TOKEN": "svc-1", "HOOK_SECRET": "s3cr3t", "WYND_RUN_API_TOKEN": "run-tok", "WYND_API_TOKEN": None}
CONTAINER_ENV = {"RECORDS_DIR": "/data/records", "SERVICE_TOKEN": "svc-1", "WYND_RUN_API_TOKEN": "run-tok"}


@pytest.fixture
def api() -> FakeRunApi:
    return FakeRunApi()


@pytest.fixture
def make_release_controller(make_controller, api):
    def make(ws, **env):
        ctl = make_controller(ws, env={**ENV, **env})
        ctl.releases = ReleaseService(ctl.ctx, ctl, run_api_client=api)
        install_backends(ctl, FakeServing(api), FakeTriggers())
        return ctl

    return make


@pytest.fixture
def ctl(workspace, make_release_controller):
    return make_release_controller(workspace)


@pytest.fixture
def head(ctl) -> str:
    return process_head(ctl.ctx, "p1")[0]


@pytest.fixture
def built(ctl, head):
    return put_build(ctl, "p1", head, MANIFEST)


def settle(ctl) -> None:
    """Wait for the release service's background warm-ups and run mirrors."""
    for thread in list(ctl.releases.threads):
        thread.join(5)


def request(head: str, trigger=None, env=None, enabled: bool = True) -> CreateReleaseRequest:
    return CreateReleaseRequest(process_id="p1", commit=head, trigger=trigger or ManualTrigger(),
                                env=BINDINGS if env is None else env, enabled=enabled)


def create(ctl, head: str, **kw) -> Release:
    release = ctl.releases.create(request(head, **kw))
    settle(ctl)
    return release


# --- create -------------------------------------------------------------------------------------------------------------

def test_only_built_processes_can_be_released(ctl, head):
    with pytest.raises(NotBuilt) as caught:
        ctl.releases.create(request(head))
    assert caught.value.code == "not_built" and "wynd build p1" in caught.value.hint
    assert ctl.ctx.docs.list(RELEASES) == []


def test_create_refuses_an_unknown_process_or_commit(ctl, head, built):
    with pytest.raises(NotFound):
        ctl.releases.create(CreateReleaseRequest(process_id="nope", commit=head, trigger=ManualTrigger()))
    with pytest.raises(NotFound):
        ctl.releases.create(request("f" * 40))


def test_create_stores_the_release_and_starts_it_warm(ctl, head, built):
    release = create(ctl, head)

    assert release.id.startswith("rel_") and release.process_id == "p1"
    assert (release.commit, release.short, release.image, release.behind) == (head, head[:7], IMAGE, 0)
    assert release.trigger == ManualTrigger() and release.env == BINDINGS and release.enabled
    assert (release.webhook_url, release.next_fire_at, release.created_at) == (None, None, T0)
    assert ctl.ctx.serving.ensured() == [(release.id, CONTAINER_ENV)]
    assert ctl.ctx.serving.manifests == [MANIFEST]
    assert ctl.ctx.triggers.calls == [("install", release.id)]
    assert ctl.releases.get(release.id).state == "serving"
    doc = ctl.ctx.docs.get(RELEASES, release.id)
    assert (doc["serving"], doc["triggers"], doc["state"]) == ("fake", "fake-triggers", "serving")
    assert doc["env"] == {"RECORDS_DIR": {"value": "/data/records"}, "SERVICE_TOKEN": {"from_env": "SVC_TOKEN"}}


def test_a_short_commit_is_resolved(ctl, head, built):
    assert create(ctl, head[:9]).commit == head


def test_a_disabled_release_is_neither_started_nor_installed(ctl, head, built):
    release = create(ctl, head, enabled=False)

    assert not release.enabled and release.state == "stopped"
    assert ctl.ctx.serving.calls == [] and ctl.ctx.triggers.calls == []


def test_a_pushed_build_is_released_by_its_digest(ctl, head):
    pushed = "localhost:5001/wynd/p1:0123456789ab@sha256:" + "e" * 64
    put_build(ctl, "p1", head, MANIFEST, pushed=[pushed])
    assert create(ctl, head).image == pushed


def test_the_process_status_shows_the_release(ctl, head, built):
    release = create(ctl, head)
    status = ctl.processes.status("p1")

    assert status.released is True
    [ref] = status.releases
    assert (ref.id, ref.commit, ref.behind, ref.trigger, ref.state) == (release.id, head, 0, "manual", "serving")


def test_behind_counts_closure_commits_since_the_release(ctl, head, built, commit):
    release = create(ctl, head)
    upper = ctl.ctx.root / "processes/p1/proto/upper.yaml"
    commit(ctl.ctx.root, "touch p1", {"processes/p1/proto/upper.yaml": upper.read_text() + "# note\n"})
    commit(ctl.ctx.root, "unrelated", {"docs/readme.md": "hello\n"})

    assert ctl.releases.get(release.id).behind == 1


# --- env binding --------------------------------------------------------------------------------------------------------

def test_every_required_var_must_be_bound(ctl, head, built):
    with pytest.raises(EnvUnbound) as caught:
        ctl.releases.create(request(head, env={"LOG_LEVEL": ValueBinding(value="debug")}))
    assert caught.value.code == "env_unbound"
    assert caught.value.details == {"unbound": ["RECORDS_DIR", "SERVICE_TOKEN"]}
    assert ctl.ctx.docs.list(RELEASES) == []


@pytest.mark.parametrize(("env", "message"), [
    ({**BINDINGS, "COLOUR": ValueBinding(value="red")}, "does not declare: COLOUR"),
    ({**BINDINGS, "SERVICE_TOKEN": ValueBinding(value="svc-1")}, "secret env var SERVICE_TOKEN must be bound with from_env"),
    ({**BINDINGS, "WYND_DATA_DIR": ValueBinding(value="/tmp")}, "WYND_DATA_DIR is set by the base image"),
    ({**BINDINGS, "RECORDS_DIR": FromEnvBinding(from_env="not a name")}, "'not a name' is not a valid env var name"),
])
def test_bad_bindings_are_invalid(ctl, head, built, env, message):
    with pytest.raises(Invalid) as caught:
        ctl.releases.create(request(head, env=env))
    assert message in caught.value.message


def test_bindings_must_be_supported_by_the_serving_backend(ctl, head, built):
    ctl.ctx.serving.supported_bindings = frozenset({"from_env"})
    with pytest.raises(Invalid) as caught:
        ctl.releases.create(request(head))
    assert "does not support value bindings" in caught.value.message


def test_one_of_groups_and_the_controller_supplied_registry_snapshot(ctl, head):
    manifest = EnvManifest(process="p1", vars=[
        EnvVar(name="ANTHROPIC_API_KEY", secret=True, required=False, one_of="claude-code-auth"),
        EnvVar(name="CLAUDE_CODE_OAUTH_TOKEN", secret=True, required=False, one_of="claude-code-auth"),
        EnvVar(name="WYND_REGISTRY_JSON", required=False, one_of="user-registry"),
    ], groups={"claude-code-auth": EnvGroup(modes=["image"]), "user-registry": EnvGroup(modes=["image"])})
    put_build(ctl, "p1", head, manifest)

    with pytest.raises(EnvUnbound) as caught:
        ctl.releases.create(request(head, env={}))
    assert sorted(caught.value.details["unbound"]) == ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"]

    release = create(ctl, head, env={"CLAUDE_CODE_OAUTH_TOKEN": FromEnvBinding(from_env="SVC_TOKEN")})
    assert ctl.releases.env_check(release.id).unbound == []


def test_the_container_gets_the_registry_snapshot_of_the_release_commit(make_workspace, make_release_controller,
                                                                        agentic_files):
    ws = make_workspace(files=agentic_files("p4", mcp="notes"))
    ctl = make_release_controller(ws)
    ctl.ctx.stores.registry.put("mcp", "notes", {"name": "notes", "transport": "http", "url": "http://mcp.test/mcp"})
    head = process_head(ctl.ctx, "p4")[0]
    manifest = EnvManifest(process="p4", vars=[EnvVar(name="WYND_REGISTRY_JSON", required=False,
                                                      one_of="user-registry")],
                           groups={"user-registry": EnvGroup(modes=["image"])})
    put_build(ctl, "p4", head, manifest)
    (ws / "processes/p4/process.yaml").write_text("kind: [broken\n")    # the working tree does not matter

    release = ctl.releases.create(CreateReleaseRequest(process_id="p4", commit=head, trigger=ManualTrigger()))
    settle(ctl)

    [(_, env)] = ctl.ctx.serving.ensured()
    assert json.loads(env["WYND_REGISTRY_JSON"])["mcp"]["notes"]["url"] == "http://mcp.test/mcp"
    assert ctl.releases.env_check(release.id).ok


# --- trigger validation -------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("trigger", "message"), [
    (ScheduleTrigger(cron="61 * * * *", inputs={"text": "x"}), "cron minute field '61'"),
    (ScheduleTrigger(cron="0 0 31 2 *", inputs={"text": "x"}), "schedule never fires"),
    (ScheduleTrigger(cron="0 7 * * *", timezone="Mars/Olympus", inputs={"text": "x"}), "unknown timezone"),
    (ScheduleTrigger(cron="0 7 * * *", inputs={"colour": "red"}), "unknown ['colour'], missing ['text']"),
    (WebhookTrigger(secret_env="not-a-name"), "secret_env 'not-a-name' is not a valid env var name"),
])
def test_invalid_triggers(ctl, head, built, trigger, message):
    with pytest.raises(Invalid) as caught:
        ctl.releases.create(request(head, trigger=trigger))
    assert message in caught.value.message


def test_a_schedule_release_reports_its_next_fire(ctl, head, built):
    trigger = ScheduleTrigger(cron="0 7 * * 1-5", timezone="Europe/London", inputs={"text": "daily"})
    release = create(ctl, head, trigger=trigger)

    assert release.trigger == trigger
    assert release.next_fire_at == datetime(2026, 9, 23, 6, 0, tzinfo=UTC)      # 07:00 BST on a Wednesday
    assert release.webhook_url is None


def test_a_webhook_release_reports_its_path(ctl, head, built):
    release = create(ctl, head, trigger=WebhookTrigger(secret_env="HOOK_SECRET"))
    assert release.webhook_url == f"/hooks/releases/{release.id}" and release.next_fire_at is None


def test_an_open_webhook_is_warned_about(ctl, head, built, caplog):
    with caplog.at_level(logging.WARNING, logger="wynd.controller.releases.service"):
        create(ctl, head, trigger=WebhookTrigger())
    assert "anyone who can reach the controller can fire it" in caplog.text


# --- firing -------------------------------------------------------------------------------------------------------------

def test_a_manual_trigger_submits_mirrors_and_records_the_fire(ctl, head, built, api):
    release = create(ctl, head)

    run = ctl.releases.trigger(release.id, {"text": "hi"}, source="manual")

    assert (run.status, run.mode, run.release_id, run.trigger, run.commit) == \
        ("running", "image", release.id, "manual", head)
    assert run.target == ReleaseTarget(release_id=release.id)
    assert ctl.runs.get(run.id).id == run.id                   # recorded before the mirror ends
    settle(ctl)

    [submit] = api.submits
    assert submit["url"] == f"http://serving.test/{release.id}"
    assert (submit["run_id"], submit["inputs"], submit["files"], submit["token"]) == \
        (run.id, {"text": "hi"}, {}, "run-tok")
    assert submit["metadata"] == {"trigger": "manual", "release_id": release.id,
                                  "target": {"kind": "release", "commit": head}}
    assert ctl.ctx.serving.ensured() == [(release.id, CONTAINER_ENV)] * 2   # warm-up, then the fire

    finished = ctl.runs.get(run.id)
    assert (finished.status, finished.exit, finished.outputs) == ("succeeded", "done", {"words": 2})
    assert finished.target == ReleaseTarget(release_id=release.id) and finished.trigger == "manual"
    assert [r.id for r in ctl.runs.list(release_id=release.id)] == [run.id]
    assert ctl.runs.events(run.id)[-1]["type"] == "run.end"

    [fire] = ctl.releases.fires(release.id)
    assert (fire.release_id, fire.source, fire.ok, fire.run_id, fire.error) == (release.id, "manual", True, run.id, None)
    assert (fire.at, fire.started_at) == (T0, T0) and fire.finished_at is not None


def test_a_schedule_fires_with_its_fixed_inputs(ctl, head, built, api):
    release = create(ctl, head, trigger=ScheduleTrigger(cron="@daily", inputs={"text": "daily"}))
    ctl.releases.trigger(release.id, None, source="schedule")
    settle(ctl)
    assert api.submits[0]["inputs"] == {"text": "daily"}
    assert api.submits[0]["metadata"]["trigger"] == "schedule"


def test_path_inputs_are_sent_as_run_api_files(make_workspace, make_release_controller, api):
    fixture = (Path(__file__).parent / "fixtures/ws_basic/processes/p1/process.yaml").read_text()
    process = fixture.replace("inputs:\n  text: string\n", "inputs:\n  text: string\n  doc: path?\n")
    assert process != fixture
    ws = make_workspace(files={"processes/p1/process.yaml": process, "docs/note.txt": "a note\n"})
    ctl = make_release_controller(ws)
    head = process_head(ctl.ctx, "p1")[0]
    put_build(ctl, "p1", head, MANIFEST)
    release = create(ctl, head)

    ctl.releases.trigger(release.id, {"text": "hi", "doc": "docs/note.txt"}, source="manual")
    settle(ctl)

    [submit] = api.submits
    [name] = submit["files"]
    assert name.endswith("-note.txt") and submit["files"][name] == b"a note\n"
    assert submit["inputs"] == {"text": "hi", "doc": {"$file": name}}


def test_a_failed_fire_is_recorded_and_raised(ctl, head, built, api):
    off = create(ctl, head, enabled=False)
    with pytest.raises(Conflict) as caught:
        ctl.releases.trigger(off.id, {"text": "hi"}, source="manual")
    assert "disabled" in caught.value.message

    unset = create(ctl, head, env={**BINDINGS, "SERVICE_TOKEN": FromEnvBinding(from_env="NOT_SET")})
    with pytest.raises(EnvMissing) as caught:
        ctl.releases.trigger(unset.id, {"text": "hi"}, source="manual")
    assert "SERVICE_TOKEN (from NOT_SET)" in caught.value.message

    [fire] = ctl.releases.fires(off.id)
    assert (fire.ok, fire.run_id) == (False, None) and "disabled" in fire.error
    [fire] = ctl.releases.fires(unset.id)
    assert fire.ok is False and "NOT_SET" in fire.error
    assert api.submits == []


def test_a_release_whose_binding_source_is_unset_reports_the_error(ctl, head, built):
    release = create(ctl, head, env={**BINDINGS, "SERVICE_TOKEN": FromEnvBinding(from_env="NOT_SET")})

    shown = ctl.releases.get(release.id)
    assert shown.state == "error" and "NOT_SET" in shown.state_detail
    check = ctl.releases.env_check(release.id)
    assert (check.ok, check.missing, check.unbound) == (False, ["SERVICE_TOKEN"], [])


def test_a_serving_failure_fails_the_fire(ctl, head, built, api):
    release = create(ctl, head)
    ctl.ctx.serving.fail = Unavailable("the Docker daemon is not reachable")
    ctl.ctx.serving.states.clear()

    with pytest.raises(Unavailable):
        ctl.releases.trigger(release.id, {"text": "hi"}, source="manual")

    [fire] = ctl.releases.fires(release.id)
    assert fire.ok is False and "Docker daemon" in fire.error
    shown = ctl.releases.get(release.id)
    assert (shown.state, shown.state_detail) == ("error", "the Docker daemon is not reachable")


def test_a_run_api_refusal_is_translated(ctl, head, built, api):
    release = create(ctl, head)
    api.refuse = RunApiError("HTTP 422 invalid_inputs: text is required", status=422, code="invalid_inputs")

    with pytest.raises(Invalid):
        ctl.releases.trigger(release.id, {}, source="manual")

    [fire] = ctl.releases.fires(release.id)
    assert fire.ok is False and "invalid_inputs" in fire.error
    assert ctl.runs.list(release_id=release.id) == []


def test_a_lost_run_is_recorded_as_failed(workspace, make_controller, api):
    class LosingClient(FakeRunApiClient):
        def events(self, run_id, *, after=None):
            raise RunApiError("HTTP 500 internal: boom", status=500, code="internal")

    ctl = make_controller(workspace, env=ENV)
    ctl.releases = ReleaseService(ctl.ctx, ctl, run_api_client=lambda url: LosingClient(api, url))
    install_backends(ctl, FakeServing(api), FakeTriggers())
    head = process_head(ctl.ctx, "p1")[0]
    put_build(ctl, "p1", head, MANIFEST)
    release = create(ctl, head)

    run = ctl.releases.trigger(release.id, {"text": "hi"}, source="manual")
    settle(ctl)

    lost = ctl.runs.get(run.id)
    assert (lost.status, lost.exit, lost.error.cause) == ("failed", "error", "internal")
    assert "boom" in lost.error.message
    [fire] = ctl.releases.fires(release.id)
    assert fire.ok is False and fire.error.startswith(f"lost run {run.id}") and fire.finished_at is not None


# --- webhooks -----------------------------------------------------------------------------------------------------------

def test_webhook_secret_env_verification(ctl, head, built):
    hook = create(ctl, head, trigger=WebhookTrigger(secret_env="HOOK_SECRET"))

    assert ctl.releases.verify_webhook(hook.id, "s3cr3t").id == hook.id
    for presented in ("wrong", None, ""):
        with pytest.raises(Unauthorized):
            ctl.releases.verify_webhook(hook.id, presented)

    unset = create(ctl, head, trigger=WebhookTrigger(secret_env="NOT_SET"))
    with pytest.raises(Unauthorized) as caught:
        ctl.releases.verify_webhook(unset.id, "anything")
    assert "NOT_SET is not set" in caught.value.message


def test_a_webhook_without_secret_env_falls_back_to_the_api_token(workspace, make_release_controller):
    ctl = make_release_controller(workspace, WYND_API_TOKEN="api-tok")
    head = process_head(ctl.ctx, "p1")[0]
    put_build(ctl, "p1", head, MANIFEST)
    hook = create(ctl, head, trigger=WebhookTrigger())

    assert ctl.releases.verify_webhook(hook.id, "api-tok").id == hook.id
    with pytest.raises(Unauthorized):
        ctl.releases.verify_webhook(hook.id, None)


def test_a_webhook_without_any_secret_is_open(ctl, head, built):
    hook = create(ctl, head, trigger=WebhookTrigger())
    assert ctl.releases.verify_webhook(hook.id, None).id == hook.id


def test_only_enabled_webhook_releases_take_hooks(ctl, head, built):
    manual = create(ctl, head)
    with pytest.raises(NotFound):
        ctl.releases.verify_webhook(manual.id, "s3cr3t")
    with pytest.raises(NotFound):
        ctl.releases.verify_webhook("rel_missing", "s3cr3t")
    off = create(ctl, head, trigger=WebhookTrigger(secret_env="HOOK_SECRET"), enabled=False)
    with pytest.raises(Conflict):
        ctl.releases.verify_webhook(off.id, "s3cr3t")


def test_webhook_fires_through_the_e2e_helper(ctl, head, built, api):
    manual_run, hook_run = fire_manual_and_webhook(ctl, "p1", head, {"text": "hi"}, env=BINDINGS,
                                                   secret_env="HOOK_SECRET")

    assert (manual_run.trigger, manual_run.status) == ("manual", "succeeded")
    assert (hook_run.trigger, hook_run.status) == ("webhook", "succeeded")
    assert [s["metadata"]["trigger"] for s in api.submits] == ["manual", "webhook"]
    assert ctl.releases.list() == []
    assert sorted(ctl.ctx.serving.removed()) == sorted([manual_run.release_id, hook_run.release_id])


# --- update, delete, list -----------------------------------------------------------------------------------------------

def test_disable_and_enable(ctl, head, built):
    release = create(ctl, head)
    ctl.ctx.triggers.calls.clear()

    off = ctl.releases.update(release.id, ReleasePatch(enabled=False))
    assert (off.enabled, off.state) == (False, "stopped")
    assert ctl.ctx.serving.removed() == [release.id]
    assert ctl.ctx.triggers.calls == [("remove", release.id)]
    with pytest.raises(Conflict):
        ctl.releases.trigger(release.id, {"text": "hi"}, source="manual")

    on = ctl.releases.update(release.id, ReleasePatch(enabled=True))
    settle(ctl)
    assert on.enabled and ctl.releases.get(release.id).state == "serving"
    assert ctl.ctx.triggers.calls[-1] == ("install", release.id)
    assert len(ctl.ctx.serving.ensured()) == 2


def test_changing_the_env_restarts_the_container(ctl, head, built):
    release = create(ctl, head)

    ctl.releases.update(release.id, ReleasePatch(env={**BINDINGS, "LOG_LEVEL": ValueBinding(value="debug")}))
    settle(ctl)

    assert ctl.ctx.serving.removed() == [release.id]
    assert ctl.ctx.serving.ensured()[-1] == (release.id, {**CONTAINER_ENV, "LOG_LEVEL": "debug"})
    assert ctl.ctx.triggers.calls == [("install", release.id)]            # the trigger did not change


def test_changing_the_trigger_reinstalls_it(ctl, head, built):
    release = create(ctl, head)
    trigger = ScheduleTrigger(cron="*/30 * * * *", inputs={"text": "x"})

    updated = ctl.releases.update(release.id, ReleasePatch(trigger=trigger))

    assert updated.trigger == trigger and updated.next_fire_at == datetime(2026, 9, 22, 12, 30, tzinfo=UTC)
    assert ctl.ctx.triggers.calls == [("install", release.id), ("install", release.id)]
    assert ctl.ctx.serving.removed() == []


def test_an_invalid_update_changes_nothing(ctl, head, built):
    release = create(ctl, head)
    before = ctl.ctx.docs.get(RELEASES, release.id)

    with pytest.raises(Invalid):
        ctl.releases.update(release.id, ReleasePatch(trigger=ScheduleTrigger(cron="nope", inputs={"text": "x"})))
    with pytest.raises(EnvUnbound):
        ctl.releases.update(release.id, ReleasePatch(env={}))

    assert ctl.ctx.docs.get(RELEASES, release.id) == before


def test_disabling_never_revalidates(ctl, head, built):
    release = create(ctl, head)
    shutil.rmtree(ctl.ctx.state_dir / "build")                             # the build is gone

    assert ctl.releases.update(release.id, ReleasePatch(enabled=False)).enabled is False
    with pytest.raises(NotBuilt):
        ctl.releases.update(release.id, ReleasePatch(enabled=True))


def test_disabling_a_release_that_is_still_starting_removes_it_once_up(ctl, head, built):
    serving = ctl.ctx.serving
    entered, gate = threading.Event(), threading.Event()
    real_ensure = serving.ensure

    def slow_ensure(release, manifest, env):
        entered.set()
        gate.wait(5)
        return real_ensure(release, manifest, env)

    serving.ensure = slow_ensure
    release = ctl.releases.create(request(head))
    assert entered.wait(5)
    disabling = threading.Thread(target=ctl.releases.update, args=(release.id, ReleasePatch(enabled=False)))
    disabling.start()
    disabling.join(0.2)
    assert disabling.is_alive() and serving.removed() == []               # waits for the start to finish
    gate.set()
    disabling.join(5)
    settle(ctl)

    assert [method for method, _, _ in serving.calls] == ["ensure", "remove"]
    assert ctl.releases.get(release.id).state == "stopped"


def test_delete_removes_container_trigger_and_record_but_keeps_fires(ctl, head, built):
    release = create(ctl, head)
    ctl.releases.trigger(release.id, {"text": "hi"}, source="manual")
    settle(ctl)

    ctl.releases.delete(release.id)

    assert ctl.ctx.serving.removed() == [release.id]
    assert ctl.ctx.triggers.calls[-1] == ("remove", release.id)
    with pytest.raises(NotFound):
        ctl.releases.get(release.id)
    with pytest.raises(NotFound):
        ctl.releases.delete(release.id)
    assert len(ctl.releases.fires(release.id)) == 1


def test_list_is_newest_first_and_filters_by_process(ctl, head, built):
    first = create(ctl, head)
    second = create(ctl, head, trigger=WebhookTrigger(secret_env="HOOK_SECRET"))

    assert [r.id for r in ctl.releases.list()] == [second.id, first.id]
    assert [r.id for r in ctl.releases.list(process_id="p1")] == [second.id, first.id]
    assert ctl.releases.list(process_id="p2") == []


def test_warm_all_starts_every_enabled_release(ctl, head, built):
    on = create(ctl, head)
    create(ctl, head, enabled=False)
    ctl.ctx.serving.calls.clear()

    ctl.releases.warm_all()
    settle(ctl)

    assert [release_id for release_id, _ in ctl.ctx.serving.ensured()] == [on.id]


# --- DockerServing ------------------------------------------------------------------------------------------------------

def release_of(id: str = "rel_1", image: str = IMAGE) -> Release:
    return Release(id=id, process_id="p1", commit="c" * 40, short="c" * 7, image=image, trigger=ManualTrigger(),
                   enabled=True, state="stopped", created_at=T0)


@pytest.fixture
def docker_serving(controller, api, monkeypatch):
    import wynd.controller.docker as docker

    fake = FakeDocker(api, {IMAGE: {"dev.wynd.run-api-port": "8081"}}).install(monkeypatch)
    restarts: list[str | None] = []
    run_detached = fake.run_detached

    def recording(*args, **kw):
        restarts.append(kw.get("restart"))
        return run_detached(*args, **kw)

    monkeypatch.setattr(docker, "run_detached", recording)
    serving = DockerServing(env=controller.ctx.env, workspace_root=controller.ctx.root,
                            state_dir=controller.ctx.state_dir, stores=controller.ctx.stores, run_api_client=api)
    return serving, fake, restarts, controller.ctx.state_dir / "serve"


def read_record(path):
    from wynd.controller.models import ServedContainer

    return ServedContainer.model_validate_json(path.read_text())


def test_docker_serving_starts_a_release_container(docker_serving):
    serving, fake, restarts, serve_dir = docker_serving

    url = serving.ensure(release_of(), MANIFEST, {"RECORDS_DIR": "/data"})

    [started] = fake.started()
    assert started == {"image": IMAGE, "name": "wynd-rel-rel_1", "env": {"RECORDS_DIR": "/data"}, "host_port": None,
                       "container_port": 8081, "labels": {"dev.wynd.release": "rel_1", "dev.wynd.served": "1"}}
    assert restarts == ["unless-stopped"]
    assert url == f"http://127.0.0.1:{fake.ports['wynd-rel-rel_1']}"
    record = read_record(serve_dir / "wynd-rel-rel_1.json")
    assert (record.release_id, record.url, record.state, record.process, record.stopped_at) == \
        ("rel_1", url, "serving", "p1", None)
    assert record.env_hash.startswith("sha256:") and "/data" not in (serve_dir / "wynd-rel-rel_1.json").read_text()
    assert serving.status(release_of()) == ("serving", None)


def test_docker_serving_reuses_a_ready_container_without_docker(docker_serving):
    serving, fake, _, _ = docker_serving
    url = serving.ensure(release_of(), MANIFEST, {"RECORDS_DIR": "/data"})
    fake.calls.clear()

    assert serving.ensure(release_of(), MANIFEST, {"RECORDS_DIR": "/data"}) == url
    assert fake.calls == []


def test_docker_serving_replaces_the_container_when_the_env_changes(docker_serving):
    serving, fake, _, serve_dir = docker_serving
    serving.ensure(release_of(), MANIFEST, {"RECORDS_DIR": "/data"})
    first = read_record(serve_dir / "wynd-rel-rel_1.json")

    serving.ensure(release_of(), MANIFEST, {"RECORDS_DIR": "/elsewhere"})

    assert fake.removed() == ["wynd-rel-rel_1"] * 2                        # before each start
    assert fake.started()[-1]["env"] == {"RECORDS_DIR": "/elsewhere"}
    [archived] = serve_dir.glob("wynd-rel-rel_1.*.json")
    old = read_record(archived)
    assert (old.env_hash, old.state, old.started_at) == (first.env_hash, "stopped", first.started_at)
    assert old.stopped_at is not None and old.stopped_at >= old.started_at
    assert read_record(serve_dir / "wynd-rel-rel_1.json").env_hash != first.env_hash


def test_docker_serving_restarts_a_container_that_stopped_answering(docker_serving, api):
    serving, fake, _, _ = docker_serving
    url = serving.ensure(release_of(), MANIFEST, {})
    api.ready.discard(url)

    assert serving.status(release_of()) == ("starting", None)             # within the ready timeout
    serving.ready_timeout_s = 0
    assert serving.status(release_of()) == ("error", "supervisor: env-check failed: RECORDS_DIR missing")
    serving.ready_timeout_s = 120
    assert serving.ensure(release_of(), MANIFEST, {}) != ""
    assert len(fake.started()) == 2


def test_docker_serving_records_a_container_that_never_becomes_ready(docker_serving):
    serving, fake, _, serve_dir = docker_serving
    fake.becomes_ready = False

    with pytest.raises(Unavailable) as caught:
        serving.ensure(release_of(), MANIFEST, {})

    assert "wynd-rel-rel_1 did not become ready" in caught.value.message
    assert "RECORDS_DIR missing" in caught.value.message
    assert read_record(serve_dir / "wynd-rel-rel_1.json").state == "error"
    assert serving.status(release_of()) == ("error", "supervisor: env-check failed: RECORDS_DIR missing")


def test_docker_serving_remove_keeps_the_uptime(docker_serving):
    serving, fake, _, serve_dir = docker_serving
    serving.ensure(release_of(), MANIFEST, {})
    fake.calls.clear()

    serving.remove("rel_1")

    assert fake.removed() == ["wynd-rel-rel_1"]
    assert not (serve_dir / "wynd-rel-rel_1.json").exists()
    [archived] = serve_dir.glob("wynd-rel-rel_1.*.json")
    record = read_record(archived)
    assert record.release_id == "rel_1" and record.stopped_at is not None and record.state == "stopped"
    assert serving.status(release_of()) == ("stopped", None)
    fake.calls.clear()
    serving.remove("rel_1")                                                # nothing running: no Docker call
    assert fake.calls == []


def test_release_containers_are_listed_but_never_acquired(docker_serving, controller, api):
    from wynd.controller.serving import ServeService

    serving, fake, _, _ = docker_serving
    serving.ensure(release_of(), MANIFEST, {})
    serve = ServeService(controller.ctx, controller, run_api_client=api)

    assert [s.name for s in serve.list()] == ["wynd-rel-rel_1"]
    fake.labels[IMAGE] = {"dev.wynd.env-manifest": EnvManifest(process="p1", vars=[]).model_dump_json()}
    url, ephemeral = serve.acquire(IMAGE)
    assert ephemeral and fake.started()[-1]["name"].startswith("wynd-run-")


def test_open_serving_backend_selects_the_entry_point(controller):
    kw = {"env": controller.ctx.env, "workspace_root": controller.ctx.root, "state_dir": controller.ctx.state_dir,
          "stores": controller.ctx.stores}
    assert isinstance(open_serving_backend("docker", **kw), DockerServing)
    assert isinstance(controller.ctx.serving, DockerServing)                # the default, selected lazily
    with pytest.raises(Invalid) as caught:
        open_serving_backend("nomad", **kw)
    assert "unknown serving backend 'nomad'" in caught.value.message

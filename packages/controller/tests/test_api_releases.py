"""Release routes and webhooks (PLAN §3.21 amendment 5; `$DRAFTS/07 §12.1` routes 34-39; `$DRAFTS/06 §8.3` (+) fires
and `POST /hooks/releases/{id}`) with the CTL-REL fake serving/trigger backends and the CTL-M2 fake run API."""

from __future__ import annotations

import pytest

from support.ctl_api_client import api_client
from support.ctl_m2_runapi import FakeRunApi
from support.ctl_rel_fakes import IMAGE, FakeServing, FakeTriggers, install_backends, put_build
from wynd.controller.releases.service import ReleaseService
from wynd.controller.status import process_head
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.fragments import EnvVar

MANIFEST = EnvManifest(process="p1", vars=[
    EnvVar(name="RECORDS_DIR"),
    EnvVar(name="SERVICE_TOKEN", secret=True),
    EnvVar(name="WYND_RUN_API_TOKEN", secret=True, required=False, used_by=["runtime"]),
])
BINDINGS = {"RECORDS_DIR": {"value": "/data/records"}, "SERVICE_TOKEN": {"from_env": "SVC_TOKEN"}}
ENV = {"SVC_TOKEN": "svc-1", "HOOK_SECRET": "s3cr3t", "WYND_RUN_API_TOKEN": "run-tok", "WYND_API_TOKEN": None}


@pytest.fixture
def api() -> FakeRunApi:
    return FakeRunApi()


@pytest.fixture
def ctl(workspace, make_controller, api):
    ctl = make_controller(workspace, env=ENV)
    ctl.releases = ReleaseService(ctl.ctx, ctl, run_api_client=api)
    install_backends(ctl, FakeServing(api), FakeTriggers())
    yield ctl
    settle(ctl)


@pytest.fixture
def client(ctl):
    return api_client(ctl)


@pytest.fixture
def head(ctl) -> str:
    return process_head(ctl.ctx, "p1")[0]


@pytest.fixture
def built(ctl, head):
    return put_build(ctl, "p1", head, MANIFEST)


def settle(ctl) -> None:
    for thread in list(ctl.releases.threads):
        thread.join(5)


def create(client, head: str, trigger: dict | None = None, **fields) -> dict:
    body = {"process_id": "p1", "commit": head, "trigger": trigger or {"kind": "manual"}, "env": BINDINGS,
            "enabled": True, **fields}
    response = client.post("/api/releases", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# --- CRUD -------------------------------------------------------------------------------------------------------------

def test_only_a_built_process_can_be_released(client, head):
    body = {"process_id": "p1", "commit": head, "trigger": {"kind": "manual"}, "env": BINDINGS, "enabled": True}
    response = client.post("/api/releases", json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "not_built"
    assert client.get("/api/releases").json() == {"releases": []}


def test_create_list_update_and_delete(client, ctl, head, built):
    release = create(client, head)
    assert (release["process_id"], release["commit"], release["short"], release["image"]) == ("p1", head, head[:7],
                                                                                              IMAGE)
    assert (release["trigger"], release["env"], release["enabled"]) == ({"kind": "manual"}, BINDINGS, True)
    settle(ctl)
    listed = client.get("/api/releases", params={"process_id": "p1"}).json()["releases"]
    assert [(r["id"], r["state"]) for r in listed] == [(release["id"], "serving")]
    assert client.get("/api/releases", params={"process_id": "p2"}).json() == {"releases": []}

    schedule = {"kind": "schedule", "cron": "0 7 * * *", "timezone": None, "inputs": {"text": "daily"}}
    patched = client.patch(f"/api/releases/{release['id']}", json={"trigger": schedule})
    assert patched.status_code == 200
    assert patched.json()["trigger"] == schedule and patched.json()["next_fire_at"] is not None
    disabled = client.patch(f"/api/releases/{release['id']}", json={"enabled": False}).json()
    assert (disabled["enabled"], disabled["state"]) == (False, "stopped")
    bad = client.patch(f"/api/releases/{release['id']}", json={"trigger": {**schedule, "cron": "61 * * * *"}})
    assert (bad.status_code, bad.json()["error"]["code"]) == (422, "invalid")

    deleted = client.delete(f"/api/releases/{release['id']}")
    assert (deleted.status_code, deleted.content) == (204, b"")
    assert client.get("/api/releases").json() == {"releases": []}
    missing = client.post(f"/api/releases/{release['id']}/trigger", json={})
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "not_found")


def test_an_unbound_required_var_is_refused(client, head, built):
    body = {"process_id": "p1", "commit": head, "trigger": {"kind": "manual"},
            "env": {"RECORDS_DIR": {"value": "/data"}}, "enabled": True}
    response = client.post("/api/releases", json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "env_unbound"


def test_env_check(client, head, built):
    release = create(client, head)
    assert client.get(f"/api/releases/{release['id']}/env-check").json() == {
        "ok": True, "missing": [], "unbound": [], "issues": []}


# --- firing -----------------------------------------------------------------------------------------------------------

def test_trigger_fires_a_run_and_records_the_fire(client, ctl, head, built, api):
    release = create(client, head)
    response = client.post(f"/api/releases/{release['id']}/trigger", json={"inputs": {"text": "hi"}})
    assert response.status_code == 201
    run = response.json()
    assert (run["status"], run["mode"], run["release_id"], run["trigger"], run["commit"]) == (
        "running", "image", release["id"], "manual", head)
    assert run["target"] == {"kind": "release", "release_id": release["id"]}

    scheduled = client.post(f"/api/releases/{release['id']}/trigger", json={"inputs": {"text": "cron"},
                                                                            "source": "schedule"}).json()
    assert scheduled["trigger"] == "schedule"
    settle(ctl)
    assert [s["inputs"] for s in api.submits] == [{"text": "hi"}, {"text": "cron"}]
    fires = client.get(f"/api/releases/{release['id']}/fires").json()["fires"]
    assert sorted((f["source"], f["run_id"], f["ok"]) for f in fires) == sorted([
        ("manual", run["id"], True), ("schedule", scheduled["id"], True)])
    assert len(client.get(f"/api/releases/{release['id']}/fires", params={"limit": 1}).json()["fires"]) == 1
    assert client.get(f"/api/runs/{run['id']}").json()["status"] == "succeeded"


# --- webhooks ---------------------------------------------------------------------------------------------------------

def test_a_webhook_needs_its_secret(workspace, make_controller, api, head, built):
    ctl = make_controller(workspace, env=ENV)
    ctl.releases = ReleaseService(ctl.ctx, ctl, run_api_client=api)
    install_backends(ctl, FakeServing(api), FakeTriggers())
    client = api_client(ctl, api_token="api-tok")                        # hooks do not take the API token
    auth = {"Authorization": "Bearer api-tok"}
    release = client.post("/api/releases", headers=auth, json={
        "process_id": "p1", "commit": head, "trigger": {"kind": "webhook", "secret_env": "HOOK_SECRET"},
        "env": BINDINGS, "enabled": True}).json()
    assert release["webhook_url"] == f"/hooks/releases/{release['id']}"
    hook = release["webhook_url"]

    assert client.post(hook, json={"text": "hi"}).status_code == 401
    assert client.post(hook, json={"text": "hi"}, headers=auth).status_code == 401
    refused = client.post(hook, json={"text": "hi"}, headers={"Authorization": "Bearer wrong"})
    assert (refused.status_code, refused.json()["error"]["code"]) == (401, "unauthorized")

    fired = client.post(hook, json={"text": "hi"}, headers={"Authorization": "Bearer s3cr3t"})
    assert fired.status_code == 201
    assert (fired.json()["trigger"], fired.json()["release_id"]) == ("webhook", release["id"])
    by_header = client.post(hook, json={"text": "again"}, headers={"X-Wynd-Token": "s3cr3t"})
    assert by_header.status_code == 201
    settle(ctl)
    assert [s["inputs"] for s in api.submits] == [{"text": "hi"}, {"text": "again"}]


def test_a_webhook_body_must_be_a_json_object(client, ctl, head, built):
    hook = create(client, head, trigger={"kind": "webhook", "secret_env": "HOOK_SECRET"})["webhook_url"]
    secret = {"Authorization": "Bearer s3cr3t"}
    not_json = client.post(hook, content=b"text=hi", headers={**secret, "Content-Type": "text/plain"})
    assert (not_json.status_code, not_json.json()["error"]["code"]) == (415, "unsupported_media_type")
    a_list = client.post(hook, json=["hi"], headers=secret)
    assert (a_list.status_code, a_list.json()["error"]["code"]) == (422, "invalid")
    assert client.post(hook, content=b"text=hi").status_code == 401            # the secret is checked first


def test_only_enabled_webhook_releases_take_hooks(client, head, built):
    manual = create(client, head)
    assert client.post(f"/hooks/releases/{manual['id']}", json={}).status_code == 404
    assert client.post("/hooks/releases/rel_nope", json={}).status_code == 404
    off = create(client, head, trigger={"kind": "webhook", "secret_env": "HOOK_SECRET"}, enabled=False)
    response = client.post(f"/hooks/releases/{off['id']}", json={}, headers={"Authorization": "Bearer s3cr3t"})
    assert (response.status_code, response.json()["error"]["code"]) == (409, "conflict")

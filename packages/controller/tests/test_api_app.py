"""The app factory (PLAN §3.21; `$DRAFTS/06 §8.1`): error envelope, bearer auth, CORS, lifespan, router order and
`serve`."""

from __future__ import annotations

import pytest
from fastapi import APIRouter

from support.ctl_api_client import api_client
from support.ctl_rel_fakes import FakeServing, FakeTriggers, install_backends
from wynd.controller.api import create_app, serve
from wynd.controller.errors import Conflict, DesignLocked
from wynd.process.errors import ProcessNotFound
from wynd.runtime.errors import InvalidProcessInputs


# --- error envelope ---------------------------------------------------------------------------------------------------

def test_a_wynd_error_becomes_the_error_envelope_with_its_status(controller, monkeypatch):
    def locked(pid):
        raise DesignLocked("process 'p1' is being edited by an assistant turn",
                           details={"chat_id": "chat_1", "turn_id": "turn_1"}, hint="wait for the turn")

    monkeypatch.setattr(controller.design, "get", locked)
    response = api_client(controller).get("/api/processes/p1/design")
    assert response.status_code == 423
    assert response.json() == {"error": {"code": "design_locked",
                                          "message": "process 'p1' is being edited by an assistant turn",
                                          "details": {"chat_id": "chat_1", "turn_id": "turn_1"},
                                          "hint": "wait for the turn"}}


def test_process_and_runtime_errors_are_translated(controller, monkeypatch):
    def missing(pid):
        raise ProcessNotFound("unknown process 'x'")

    def bad_inputs(req, **kw):
        raise InvalidProcessInputs([{"loc": ["text"], "msg": "Field required"}])

    monkeypatch.setattr(controller.processes, "get", missing)
    monkeypatch.setattr(controller.runs, "start", bad_inputs)
    client = api_client(controller)

    response = client.get("/api/processes/x")
    assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found")
    response = client.post("/api/runs", json={"process_id": "p1", "inputs": {}})
    assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid")
    assert response.json()["error"]["details"] == {"errors": [{"loc": ["text"], "msg": "Field required"}]}


def test_request_validation_errors_are_invalid(controller):
    response = api_client(controller).post("/api/runs", json={"inputs": {}})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid" and "process_id" in error["message"]
    assert error["details"][0]["loc"] == ["body", "process_id"]


def test_unknown_paths_and_methods_keep_the_envelope(controller):
    client = api_client(controller)
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found", "details": None, "hint": None}}
    response = client.delete("/api/meta")
    assert (response.status_code, response.json()["error"]["code"]) == (405, "method_not_allowed")


def test_an_unexpected_exception_is_a_500_envelope(controller, monkeypatch):
    def boom():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(controller, "meta", boom)
    response = api_client(controller).get("/api/meta")
    assert response.status_code == 500
    assert response.json()["error"] == {"code": "internal", "message": "disk on fire", "details": None, "hint": None}


# --- auth -------------------------------------------------------------------------------------------------------------

def test_without_a_token_every_route_is_open(controller):
    assert api_client(controller).get("/api/processes").status_code == 200


def test_a_token_guards_the_api_but_not_health_the_oauth_callback_hooks_or_static(controller):
    client = api_client(controller, api_token="s3cret")
    response = client.get("/api/processes")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert client.get("/api/processes", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/processes", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/api/processes", headers={"Authorization": "bearer s3cret"}).status_code == 200

    assert client.get("/api/health").status_code == 200
    callback = client.get("/api/oauth/callback", params={"state": "nope", "code": "c"})
    assert callback.status_code == 422 and "unknown or expired OAuth state" in callback.text
    assert client.post("/hooks/releases/rel_nope", json={}).status_code == 404       # release secrets, not the token
    assert client.get("/").status_code == 200


def test_the_access_token_query_parameter_is_accepted_on_event_streams_only(controller):
    client = api_client(controller, api_token="s3cret")
    assert client.get("/api/processes", params={"access_token": "s3cret"}).status_code == 401
    response = client.get("/api/runs/run_nope/events", params={"access_token": "s3cret"})
    assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found")
    assert client.get("/api/runs/run_nope/events", params={"access_token": "wrong"}).status_code == 401


def test_the_token_defaults_to_wynd_api_token_from_the_controller_env(workspace, make_controller):
    ctl = make_controller(workspace, env={"WYND_API_TOKEN": "from-env"})
    client = api_client(ctl)
    assert client.get("/api/meta").status_code == 401
    assert client.get("/api/meta", headers={"Authorization": "Bearer from-env"}).status_code == 200
    explicit = api_client(ctl, api_token="explicit")
    assert explicit.get("/api/meta", headers={"Authorization": "Bearer explicit"}).status_code == 200


# --- CORS -------------------------------------------------------------------------------------------------------------

def preflight(client, origin: str):
    return client.options("/api/processes", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                     "Access-Control-Request-Headers": "content-type"})


def test_cors_allows_the_vite_dev_server_even_with_a_token(controller):
    client = api_client(controller, api_token="s3cret")
    response = preflight(client, "http://localhost:5173")
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert preflight(client, "http://127.0.0.1:5173").status_code == 200
    assert preflight(client, "http://evil.example").status_code == 400
    refused = client.get("/api/processes", headers={"Origin": "http://localhost:5173"})
    assert refused.status_code == 401
    assert refused.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_serve_reads_wynd_cors_origins_when_no_origins_are_given(workspace, make_controller, monkeypatch):
    import uvicorn

    ctl = make_controller(workspace, env={"WYND_CORS_ORIGINS": "http://a.example, http://b.example"})
    served = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app, **kw))
    serve(ctl, port=9999, api_token="t", scheduler=False, log_level="warning")

    assert (served["host"], served["port"], served["log_level"]) == ("127.0.0.1", 9999, "warning")
    from fastapi.testclient import TestClient

    client = TestClient(served["app"])
    assert preflight(client, "http://b.example").headers["access-control-allow-origin"] == "http://b.example"
    assert preflight(client, "http://localhost:5173").status_code == 400
    assert client.get("/api/meta").status_code == 401                          # api_token was passed through


# --- lifespan ---------------------------------------------------------------------------------------------------------

def test_the_lifespan_starts_reconciles_and_stops_the_triggers_and_closes_the_controller(controller, monkeypatch):
    from fastapi.testclient import TestClient

    serving, triggers = FakeServing(), FakeTriggers()
    install_backends(controller, serving, triggers)
    closed, warmed = [], []
    monkeypatch.setattr(controller, "close", lambda: closed.append(True))
    monkeypatch.setattr(controller.releases, "warm_all", lambda: warmed.append(True))

    with TestClient(create_app(controller)) as client:
        assert client.get("/api/health").status_code == 200
        assert triggers.calls == [("start", None), ("reconcile", [])] and warmed == [True]
    assert triggers.calls[-1] == ("stop", None) and closed == [True]


def test_without_the_scheduler_the_triggers_are_only_reconciled(controller, monkeypatch):
    from fastapi.testclient import TestClient

    triggers = FakeTriggers()
    install_backends(controller, FakeServing(), triggers)
    monkeypatch.setattr(controller, "close", lambda: None)
    with TestClient(create_app(controller, scheduler=False)):
        pass
    assert triggers.calls == [("reconcile", [])]


# --- router order -----------------------------------------------------------------------------------------------------

def test_optimise_routes_and_suffixed_routes_come_before_the_process_catch_all(controller, monkeypatch):
    import wynd.controller.optimise as optimise

    router = APIRouter()

    @router.get("/api/processes/{process_id:path}/optimise")
    def report(process_id: str):
        return {"optimise": process_id}

    monkeypatch.setattr(optimise, "router", router)
    client = api_client(controller)
    assert client.get("/api/processes/p1/optimise").json() == {"optimise": "p1"}
    assert client.get("/api/processes/p1/status").json()["head"] is not None
    assert client.get("/api/processes/p1").json()["id"] == "p1"


def test_a_conflict_from_a_service_is_409(controller, monkeypatch):
    def refuse(pid, **kw):
        raise Conflict("process 'p1' has no commit yet", hint="commit it first")

    monkeypatch.setattr(controller.jobs, "submit_bake", refuse)
    response = api_client(controller).post("/api/processes/p1/bake")
    assert response.status_code == 409
    assert response.json()["error"] == {"code": "conflict", "message": "process 'p1' has no commit yet",
                                        "details": None, "hint": "commit it first"}


@pytest.mark.parametrize("path", ["/api/docs", "/api/openapi.json"])
def test_the_api_docs_live_under_api(controller, path):
    client = api_client(controller)
    assert client.get(path).status_code == 200
    assert client.get(path.replace("/api", "")).status_code == 404

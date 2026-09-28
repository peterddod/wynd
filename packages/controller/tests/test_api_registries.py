"""Registry routes (PLAN §3.21 amendment 8; `$DRAFTS/06 §8.3`): MCP servers with the OAuth start/callback, providers
and image registries, stored in the test's `WYND_HOME` registry."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

from support.ctl_api_client import api_client
from wynd.runtime.mcp.entry import McpServerEntry

GITHUB = {"name": "github", "transport": "http", "url": "https://api.githubcopilot.com/mcp/",
          "headers": {"Authorization": "Bearer ${env:GITHUB_TOKEN}"}, "description": "GitHub"}


@pytest.fixture
def client(controller):
    return api_client(controller)


# --- MCP --------------------------------------------------------------------------------------------------------------

def test_add_list_and_remove_an_mcp_server(client, controller):
    assert client.get("/api/registries/mcp").json() == {"items": []}
    response = client.post("/api/registries/mcp", json=GITHUB)
    assert response.status_code == 201
    assert response.json()["auth_env"] == ["GITHUB_TOKEN"]                   # derived from the header reference
    items = client.get("/api/registries/mcp").json()["items"]
    assert [(i["name"], i["url"], i["timeout_s"]) for i in items] == [("github", GITHUB["url"], 30.0)]
    assert controller.ctx.stores.registry.get("mcp", "github")["auth_env"] == ["GITHUB_TOKEN"]

    assert client.delete("/api/registries/mcp/github").json() == {"removed": True, "referenced_by": []}
    assert client.delete("/api/registries/mcp/github").json() == {"removed": False, "referenced_by": []}


def test_an_invalid_mcp_server_is_refused(client):
    response = client.post("/api/registries/mcp", json={**GITHUB, "url": "ftp://example.com"})
    assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid")
    response = client.post("/api/registries/mcp", json={**GITHUB, "colour": "red"})
    assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid")


def test_oauth_start_registers_the_callback_of_this_server(client, controller, monkeypatch):
    calls = []

    def start(name, *, redirect_uri, return_to=None):
        calls.append((name, redirect_uri, return_to))
        return "https://auth.example.com/authorize?state=st_1"

    monkeypatch.setattr(controller.registries, "oauth_start", start)
    response = client.post("/api/registries/mcp/github/oauth/start", json={"return_to": "http://localhost:5173/"})
    assert response.json() == {"authorize_url": "https://auth.example.com/authorize?state=st_1"}
    assert client.post("/api/registries/mcp/github/oauth/start").status_code == 200
    assert calls == [("github", "http://testserver/api/oauth/callback", "http://localhost:5173/"),
                     ("github", "http://testserver/api/oauth/callback", None)]


def test_oauth_start_of_an_unknown_server_is_404(client):
    response = client.post("/api/registries/mcp/nope/oauth/start", json={})
    assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found")


def completing(controller, monkeypatch, return_to):
    def complete(state, code):
        assert (state, code) == ("st_1", "code_1")
        return McpServerEntry(name="github", transport="http", url=GITHUB["url"]), return_to

    monkeypatch.setattr(controller.registries, "oauth_complete", complete)


def test_the_callback_redirects_back_to_return_to(client, controller, monkeypatch):
    completing(controller, monkeypatch, "http://localhost:5173/?process=p1#graph")
    response = client.get("/api/oauth/callback", params={"state": "st_1", "code": "code_1"}, follow_redirects=False)
    assert response.status_code == 302
    location = urlsplit(response.headers["location"])
    assert (location.netloc, location.path, location.fragment) == ("localhost:5173", "/", "graph")
    assert parse_qs(location.query) == {"process": ["p1"], "mcp": ["github"], "ok": ["1"]}


def test_the_callback_without_return_to_answers_a_page(client, controller, monkeypatch):
    completing(controller, monkeypatch, None)
    response = client.get("/api/oauth/callback", params={"state": "st_1", "code": "code_1"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert "MCP server github is connected" in response.text


def test_a_refused_or_expired_sign_in_is_a_page(client):
    refused = client.get("/api/oauth/callback", params={"state": "st_1", "error": "access_denied",
                                                          "error_description": "<no>"})
    assert refused.status_code == 400 and "access_denied: &lt;no&gt;" in refused.text
    expired = client.get("/api/oauth/callback", params={"state": "st_gone", "code": "c"})
    assert expired.status_code == 422 and "unknown or expired OAuth state" in expired.text


# --- providers --------------------------------------------------------------------------------------------------------

def test_list_add_and_remove_provider_tiers(client, controller):
    providers = {p["name"]: p for p in client.get("/api/providers").json()["providers"]}
    assert {"anthropic", "claude-code", "fake"} <= set(providers)
    assert providers["claude-code"]["kind"] == "agent"
    assert providers["claude-code"]["tiers"] == {"cheap": "haiku", "standard": "sonnet", "strong": "opus"}
    assert providers["fake"]["ready"] is True

    response = client.post("/api/providers", json={"name": "claude-code", "tiers": {"cheap": "sonnet"}})
    assert response.status_code == 201
    assert response.json()["tiers"] == {"cheap": "sonnet", "standard": "sonnet", "strong": "opus"}
    assert controller.ctx.stores.registry.get("providers", "claude-code") == {"tiers": {"cheap": "sonnet"}}

    removed = client.delete("/api/providers/claude-code").json()
    assert removed["removed"] is True
    assert {p["name"]: p for p in client.get("/api/providers").json()["providers"]}["claude-code"]["tiers"][
        "cheap"] == "haiku"


def test_a_provider_body_has_no_env_and_known_names_only(client):
    with_env = client.post("/api/providers", json={"name": "claude-code", "env": {"X": "1"}})
    assert (with_env.status_code, with_env.json()["error"]["code"]) == (422, "invalid")
    unknown = client.post("/api/providers", json={"name": "nope"})
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (422, "invalid")


# --- image registries -------------------------------------------------------------------------------------------------

def test_add_list_and_remove_image_registries(client):
    local = {"name": "local", "url": "localhost:5001/wynd", "insecure": True, "default": True}
    ghcr = {"name": "ghcr", "url": "ghcr.io/acme", "password_env": "GHCR_TOKEN", "default": True}
    assert client.post("/api/registries/images", json=local).status_code == 201
    assert client.post("/api/registries/images", json=ghcr).status_code == 201
    items = {i["name"]: i for i in client.get("/api/registries/images").json()["items"]}
    assert (items["ghcr"]["default"], items["local"]["default"]) == (True, False)     # one default
    assert client.delete("/api/registries/images/local").json() == {"removed": True, "referenced_by": []}
    assert [i["name"] for i in client.get("/api/registries/images").json()["items"]] == ["ghcr"]
    bad = client.post("/api/registries/images", json={"name": "x", "url": " "})
    assert (bad.status_code, bad.json()["error"]["code"]) == (422, "invalid")

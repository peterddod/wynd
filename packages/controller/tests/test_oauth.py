"""MCP OAuth (PLAN §8.1 oauth row; `$DRAFTS/06 §5.13`) against a fake authorization server on
`httpx.MockTransport`: discovery (RFC 9728 -> 8414), dynamic registration (RFC 7591), PKCE S256 checked by the
server, state expiry, token exchange -> secrets and entry, refresh."""

from __future__ import annotations

import json
import logging
import os
import stat
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import wynd.controller.oauth as oauth
from wynd.controller.errors import Invalid, NotFound, Unavailable
from wynd.runtime.mcp.entry import McpServerEntry, resolve_env_refs
from wynd.runtime.storage.local import FileRegistry

MCP_URL = "https://mcp.example.com/mcp"
AUTH = "https://auth.example.com"
REDIRECT = "http://127.0.0.1:8780/api/oauth/callback"


class FakeAuthServer:
    """The MCP server's protected-resource metadata plus an authorization server that checks PKCE like a real one."""

    def __init__(self) -> None:
        self.issuer = AUTH
        self.resource_metadata = True                      # RFC 9728 document at the MCP server's origin
        self.server_metadata = True                        # RFC 8414 document for the issuer
        self.registration = True                           # RFC 7591 endpoint advertised
        self.requests: list[tuple[str, str]] = []
        self.registrations: list[dict] = []
        self.token_forms: list[dict[str, str]] = []
        self.codes: dict[str, dict[str, str]] = {}        # code -> the authorize request's query
        self.valid_refresh: set[str] = set()               # a refresh grant spends its token when it rotates
        self.issued = 0
        self.refresh_tokens = True
        self.fail_tokens = 0                               # status the token endpoint answers with (0 = succeed)

    def metadata_url(self) -> str:
        parts = urlsplit(self.issuer)
        return f"{parts.scheme}://{parts.netloc}/.well-known/oauth-authorization-server{parts.path.rstrip('/')}"

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append((request.method, url))
        match request.method, url:
            case "GET", "https://mcp.example.com/.well-known/oauth-protected-resource" if self.resource_metadata:
                return httpx.Response(200, json={"resource": MCP_URL, "authorization_servers": [self.issuer]})
            case "GET", _ if url == self.metadata_url() and self.server_metadata:
                metadata = {"issuer": self.issuer, "authorization_endpoint": f"{AUTH}/authorize",
                            "token_endpoint": f"{AUTH}/token", "code_challenge_methods_supported": ["S256"]}
                if self.registration:
                    metadata["registration_endpoint"] = f"{AUTH}/register"
                return httpx.Response(200, json=metadata)
            case "POST", "https://auth.example.com/register":
                self.registrations.append(json.loads(request.content))
                return httpx.Response(201, json={"client_id": f"client-{len(self.registrations)}"})
            case "POST", "https://auth.example.com/token":
                return self.token(query_of(f"?{request.content.decode()}"))
        return httpx.Response(404)

    def token(self, form: dict[str, str]) -> httpx.Response:
        self.token_forms.append(form)
        if self.fail_tokens:
            return httpx.Response(self.fail_tokens, json={"error": "invalid_grant", "error_description": "no"})
        match form.get("grant_type"):
            case "authorization_code":
                asked = self.codes.pop(form.get("code", ""), None) or {}
                expected = (asked.get("code_challenge"), asked.get("redirect_uri"), asked.get("client_id"))
                presented = (oauth.pkce_challenge(form["code_verifier"]), form["redirect_uri"], form["client_id"])
                if not asked or presented != expected:
                    return httpx.Response(400, json={"error": "invalid_grant"})
            case "refresh_token":
                if form.get("refresh_token") not in self.valid_refresh:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                if self.refresh_tokens:
                    self.valid_refresh.discard(form["refresh_token"])
            case _:
                return httpx.Response(400, json={"error": "unsupported_grant_type"})
        self.issued += 1
        tokens = {"access_token": f"at-{self.issued}", "token_type": "Bearer", "expires_in": 3600}
        if self.refresh_tokens:
            tokens["refresh_token"] = f"rt-{self.issued}"
            self.valid_refresh.add(tokens["refresh_token"])
        return httpx.Response(200, json=tokens)

    def authorize(self, authorize_url: str) -> tuple[str, str]:
        """What the user's browser does: sign in, get redirected back with (code, state)."""
        query = query_of(authorize_url)
        code = f"code-{len(self.token_forms)}-{len(self.codes)}"
        self.codes[code] = query
        return code, query["state"]


@pytest.fixture
def server(monkeypatch) -> FakeAuthServer:
    fake = FakeAuthServer()
    monkeypatch.setattr(oauth, "http_client", lambda: httpx.Client(transport=httpx.MockTransport(fake.handler)))
    monkeypatch.setattr(oauth, "_pending", {})
    return fake


@pytest.fixture
def clock(monkeypatch, fake_clock):
    monkeypatch.setattr(oauth, "now", fake_clock)
    return fake_clock


@pytest.fixture
def registry(tmp_path) -> FileRegistry:
    return FileRegistry(tmp_path / "home")


def add(registry: FileRegistry, name: str = "linear", **kw) -> McpServerEntry:
    entry = McpServerEntry(name=name, transport="http", url=MCP_URL, **kw)
    registry.put("mcp", name, entry.model_dump(mode="json"))
    return entry


def query_of(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


# --- oauth_start ----------------------------------------------------------------------------------------------------

def test_start_discovers_registers_and_builds_the_authorize_url(server, registry, clock):
    add(registry)
    url = oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT, return_to="/settings")
    assert server.requests == [
        ("GET", "https://mcp.example.com/.well-known/oauth-protected-resource"),
        ("GET", "https://auth.example.com/.well-known/oauth-authorization-server"),
        ("POST", "https://auth.example.com/register"),
    ]
    assert server.registrations == [{
        "client_name": "Wynd", "redirect_uris": [REDIRECT], "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"], "token_endpoint_auth_method": "none"}]
    assert url.startswith(f"{AUTH}/authorize?")
    query = query_of(url)
    assert {k: query[k] for k in ("response_type", "client_id", "redirect_uri", "code_challenge_method",
                                  "resource")} == {
        "response_type": "code", "client_id": "client-1", "redirect_uri": REDIRECT, "code_challenge_method": "S256",
        "resource": MCP_URL}
    challenge = query["code_challenge"]
    assert len(challenge) == 43 and "=" not in challenge                    # base64url(sha256) without padding
    assert len(query["state"]) >= 32
    second = query_of(oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT))
    assert second["state"] != query["state"] and second["code_challenge"] != challenge   # fresh per sign-in


def test_pkce_challenge_matches_rfc7636_appendix_b():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert oauth.pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_without_resource_metadata_the_origin_is_the_issuer(server, registry, clock):
    server.resource_metadata = False
    server.issuer = "https://mcp.example.com"
    add(registry)
    url = oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT)
    assert server.requests[:2] == [
        ("GET", "https://mcp.example.com/.well-known/oauth-protected-resource"),      # 404
        ("GET", "https://mcp.example.com/.well-known/oauth-authorization-server"),
    ]
    assert query_of(url)["client_id"] == "client-1"


def test_issuer_path_goes_after_the_well_known_prefix(server, registry, clock):
    server.issuer = f"{AUTH}/tenants/acme/"                                  # RFC 8414 §3.1
    add(registry)
    oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT)
    assert server.requests[1] == ("GET", f"{AUTH}/.well-known/oauth-authorization-server/tenants/acme")


def test_a_stored_client_is_reused_for_its_redirect_uri(server, registry, clock):
    add(registry, oauth={"client_id": "kept", "token_endpoint": f"{AUTH}/token", "redirect_uri": REDIRECT})
    assert query_of(oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT))["client_id"] == "kept"
    assert server.registrations == []
    other = "http://localhost:9999/api/oauth/callback"                       # serve-api reached another way
    assert query_of(oauth.oauth_start(registry, "linear", redirect_uri=other))["client_id"] == "client-1"
    assert server.registrations[0]["redirect_uris"] == [other]


def test_without_registration_a_client_id_must_be_stored(server, registry, clock):
    server.registration = False
    add(registry)
    with pytest.raises(Invalid, match="no dynamic client registration"):
        oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT)
    add(registry, oauth={"client_id": "preregistered"})                     # set by hand: no redirect_uri stored
    assert query_of(oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT))["client_id"] == "preregistered"


def test_start_needs_a_known_http_server(server, registry, clock):
    with pytest.raises(NotFound, match="unknown MCP server 'nope'"):
        oauth.oauth_start(registry, "nope", redirect_uri=REDIRECT)
    stdio = McpServerEntry(name="files", transport="stdio", command=["mcp-files"])
    registry.put("mcp", "files", stdio.model_dump(mode="json"))
    with pytest.raises(Invalid, match="OAuth needs an http server"):
        oauth.oauth_start(registry, "files", redirect_uri=REDIRECT)
    assert server.requests == []


def test_start_without_server_metadata_is_unavailable(server, registry, clock):
    server.server_metadata = False
    add(registry)
    metadata_url = "https://auth.example.com/.well-known/oauth-authorization-server"
    with pytest.raises(Unavailable, match=f"no OAuth authorization server metadata at {metadata_url}"):
        oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT)
    assert oauth._pending == {}


# --- oauth_complete -------------------------------------------------------------------------------------------------

def test_complete_exchanges_the_code_and_stores_tokens_as_secrets(server, registry, clock):
    add(registry, "linear-app", headers={"Authorization": "Bearer ${env:LINEAR_KEY}", "X-Team": "${env:TEAM}"},
        auth_env=["LINEAR_KEY", "TEAM"], description="Linear")
    code, state = server.authorize(oauth.oauth_start(registry, "linear-app", redirect_uri=REDIRECT,
                                                     return_to="http://127.0.0.1:5173/settings"))
    entry, return_to = oauth.oauth_complete(registry, state, code)

    assert return_to == "http://127.0.0.1:5173/settings"
    form = server.token_forms[0]                                             # the server checked the verifier
    assert {k: form[k] for k in ("grant_type", "code", "redirect_uri", "client_id", "resource")} == {
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT, "client_id": "client-1",
        "resource": MCP_URL}
    assert 43 <= len(form["code_verifier"]) <= 128
    assert registry.secrets() == {"WYND_MCP_LINEAR_APP_TOKEN": "at-1", "WYND_MCP_LINEAR_APP_REFRESH_TOKEN": "rt-1"}
    assert stat.S_IMODE(os.stat(registry.home / "secrets.env").st_mode) == 0o600

    assert entry.headers == {"X-Team": "${env:TEAM}", "Authorization": "Bearer ${env:WYND_MCP_LINEAR_APP_TOKEN}"}
    assert entry.auth_env == ["TEAM", "WYND_MCP_LINEAR_APP_TOKEN"]          # the replaced LINEAR_KEY is gone
    assert entry.oauth == {"client_id": "client-1", "token_endpoint": f"{AUTH}/token",
                           "expires_at": "2026-09-22T13:00:00Z", "refresh_env": "WYND_MCP_LINEAR_APP_REFRESH_TOKEN",
                           "redirect_uri": REDIRECT}
    assert entry.description == "Linear" and entry.url == MCP_URL
    assert McpServerEntry.model_validate(registry.get("mcp", "linear-app")) == entry
    env = {**registry.secrets(), "TEAM": "t"}
    assert resolve_env_refs(entry.headers["Authorization"], env, server=entry.name) == "Bearer at-1"


def test_complete_without_refresh_token_or_expiry(server, registry, clock, monkeypatch):
    server.refresh_tokens = False
    original = server.token
    monkeypatch.setattr(server, "token", lambda form: _without_expiry(original(form)))
    add(registry)
    code, state = server.authorize(oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT))
    entry, return_to = oauth.oauth_complete(registry, state, code)
    assert return_to is None
    assert registry.secrets() == {"WYND_MCP_LINEAR_TOKEN": "at-1"}
    assert (entry.oauth["expires_at"], entry.oauth["refresh_env"]) == (None, None)
    assert oauth.refresh_tokens(registry) == []                             # nothing to refresh without an expiry


def _without_expiry(response: httpx.Response) -> httpx.Response:
    body = response.json()
    body.pop("expires_in", None)
    return httpx.Response(response.status_code, json=body)


def test_state_is_single_use(server, registry, clock):
    add(registry)
    code, state = server.authorize(oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT))
    oauth.oauth_complete(registry, state, code)
    with pytest.raises(Invalid, match="unknown or expired OAuth state"):
        oauth.oauth_complete(registry, state, code)
    with pytest.raises(Invalid, match="unknown or expired OAuth state"):
        oauth.oauth_complete(registry, "forged", code)
    assert len(server.token_forms) == 1


@pytest.mark.parametrize(("elapsed", "ok"), [(599, True), (600, False), (601, False)])
def test_state_expires_after_ten_minutes(server, registry, clock, elapsed, ok):
    add(registry)
    code, state = server.authorize(oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT))
    clock.advance(elapsed)
    if ok:
        assert oauth.oauth_complete(registry, state, code)[0].oauth["client_id"] == "client-1"
        return
    with pytest.raises(Invalid, match="unknown or expired OAuth state"):
        oauth.oauth_complete(registry, state, code)
    assert server.token_forms == [] and registry.secrets() == {}


def test_expired_states_are_dropped(server, registry, clock):
    add(registry)
    oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT)
    clock.advance(601)
    oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT)
    assert len(oauth._pending) == 1


@pytest.mark.parametrize(("status", "error"), [(400, Invalid), (503, Unavailable)])
def test_a_rejected_exchange_stores_nothing(server, registry, clock, status, error):
    before = add(registry)
    code, state = server.authorize(oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT))
    server.fail_tokens = status
    with pytest.raises(error, match=f"answered {status} \\(invalid_grant: no\\)"):
        oauth.oauth_complete(registry, state, code)
    assert registry.secrets() == {}
    assert McpServerEntry.model_validate(registry.get("mcp", "linear")) == before


def test_a_wrong_verifier_is_rejected_by_the_server(server, registry, clock):
    add(registry)
    code, state = server.authorize(oauth.oauth_start(registry, "linear", redirect_uri=REDIRECT))
    server.codes[code]["code_challenge"] = oauth.pkce_challenge("some-other-verifier")
    with pytest.raises(Invalid, match="answered 400"):
        oauth.oauth_complete(registry, state, code)


# --- refresh_tokens -------------------------------------------------------------------------------------------------

def signed_in(server: FakeAuthServer, registry: FileRegistry, name: str) -> McpServerEntry:
    add(registry, name)
    code, state = server.authorize(oauth.oauth_start(registry, name, redirect_uri=REDIRECT))
    return oauth.oauth_complete(registry, state, code)[0]


def test_refresh_renews_only_tokens_expiring_within_a_minute(server, registry, clock):
    signed_in(server, registry, "linear")                                    # expires 13:00:00
    clock.advance(1800)
    signed_in(server, registry, "notion")                                    # expires 13:30:00
    add(registry, "github", headers={"Authorization": "Bearer ${env:GITHUB_TOKEN}"})
    requests = len(server.requests)

    clock.advance(1800 - 61)                                                 # 12:58:59: linear has 61 s left
    assert oauth.refresh_tokens(registry) == []
    assert len(server.requests) == requests                                  # nothing due: no network at all

    clock.advance(2)                                                         # 59 s left
    assert oauth.refresh_tokens(registry) == ["linear"]
    form = server.token_forms[-1]
    assert form == {"grant_type": "refresh_token", "refresh_token": "rt-1", "client_id": "client-1",
                    "resource": MCP_URL}
    secrets = registry.secrets()
    assert (secrets["WYND_MCP_LINEAR_TOKEN"], secrets["WYND_MCP_LINEAR_REFRESH_TOKEN"]) == ("at-3", "rt-3")
    assert secrets["WYND_MCP_NOTION_TOKEN"] == "at-2"
    linear = McpServerEntry.model_validate(registry.get("mcp", "linear"))
    assert linear.oauth["expires_at"] == "2026-09-22T13:59:01Z"
    assert linear.headers == {"Authorization": "Bearer ${env:WYND_MCP_LINEAR_TOKEN}"}
    assert oauth.refresh_tokens(registry) == []                             # renewed: not due any more
    assert registry.get("mcp", "github")["oauth"] is None


def test_refresh_keeps_the_refresh_token_when_none_is_rotated(server, registry, clock):
    signed_in(server, registry, "linear")
    server.refresh_tokens = False
    clock.advance(3600)
    assert oauth.refresh_tokens(registry) == ["linear"]
    assert registry.secrets()["WYND_MCP_LINEAR_REFRESH_TOKEN"] == "rt-1"
    clock.advance(3600)
    assert oauth.refresh_tokens(registry) == ["linear"]                      # the kept one still works
    assert registry.get("mcp", "linear")["oauth"]["refresh_env"] == "WYND_MCP_LINEAR_REFRESH_TOKEN"


def test_refresh_failures_are_logged_and_skipped(server, registry, clock, caplog):
    signed_in(server, registry, "linear")
    signed_in(server, registry, "notion")
    registry.put_secret("WYND_MCP_LINEAR_REFRESH_TOKEN", "revoked")
    entry = registry.get("mcp", "notion")
    registry.put("mcp", "orphan", {**entry, "name": "orphan",
                                   "oauth": {**entry["oauth"], "refresh_env": "WYND_MCP_ORPHAN_REFRESH_TOKEN"}})
    clock.advance(3600)
    with caplog.at_level(logging.WARNING, logger="wynd.controller.oauth"):
        assert oauth.refresh_tokens(registry) == ["notion"]
    messages = [r.getMessage() for r in caplog.records]
    assert any("'linear'" in m and "answered 400 (invalid_grant)" in m for m in messages)
    assert any("'orphan'" in m and "no refresh token is stored" in m for m in messages)
    assert registry.secrets()["WYND_MCP_LINEAR_TOKEN"] == "at-1"            # unchanged
    assert all("revoked" not in m and "rt-" not in m for m in messages)       # secrets never logged


# --- through the controller -----------------------------------------------------------------------------------------

def test_controller_signs_in_and_refreshes_before_resolving_env(server, clock, controller):
    registries = controller.registries
    registries.add_mcp(McpServerEntry(name="linear", transport="http", url=MCP_URL))
    code, state = server.authorize(registries.oauth_start("linear", redirect_uri=REDIRECT, return_to=None))
    entry, _ = registries.oauth_complete(state, code)
    assert registries.list_mcp() == [entry]
    assert controller.env.resolve()["WYND_MCP_LINEAR_TOKEN"] == "at-1"
    clock.advance(3600 - 30)
    assert controller.env.resolve()["WYND_MCP_LINEAR_TOKEN"] == "at-2"       # EnvService refreshes first

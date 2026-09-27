"""MCP OAuth (PLAN §8.1 oauth row; `$DRAFTS/06 §5.13`).

`oauth_start` discovers the authorization server (RFC 9728 protected-resource metadata at the MCP server's origin,
else the origin itself as issuer; then RFC 8414 server metadata), registers a public client (RFC 7591) unless one is
stored for the same redirect URI, and returns the authorize URL with a PKCE S256 challenge. The pending state is kept
in memory for 10 minutes and is single-use.

`oauth_complete` exchanges the code and stores the tokens with `Registry.put_secret` as `WYND_MCP_<NAME>_TOKEN` and
`WYND_MCP_<NAME>_REFRESH_TOKEN` (`NAME = name.upper().replace("-", "_")`). The entry gets
`headers["Authorization"] = "Bearer ${env:WYND_MCP_<NAME>_TOKEN}"`, `auth_env` = the env vars its headers reference,
and `oauth = {client_id, token_endpoint, expires_at, refresh_env, redirect_uri}` (`redirect_uri` is the one the client
was registered with).

`refresh_tokens` refreshes every token that expires within 60 s; failures are logged and skipped.

`http_client()` and `now()` are the module's only network access and clock; tests substitute them.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit

import httpx

from wynd.controller.errors import Invalid, NotFound, Unavailable
from wynd.runtime.mcp.entry import ENV_REF, McpServerEntry

if TYPE_CHECKING:
    from wynd.runtime.storage.base import Registry

MCP = "mcp"
STATE_TTL = timedelta(minutes=10)
REFRESH_WITHIN = timedelta(seconds=60)
HTTP_TIMEOUT_S = 15.0
RESOURCE_METADATA = "/.well-known/oauth-protected-resource"
SERVER_METADATA = "/.well-known/oauth-authorization-server"

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Pending:
    name: str
    client_id: str
    verifier: str
    redirect_uri: str
    return_to: str | None
    token_endpoint: str
    resource: str
    expires: datetime


_pending: dict[str, _Pending] = {}
_pending_lock = threading.Lock()
_refresh_lock = threading.Lock()


def http_client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT_S, follow_redirects=True, headers={"Accept": "application/json"})


def now() -> datetime:
    return datetime.now(UTC)


def token_env(name: str) -> str:
    return f"WYND_MCP_{name.upper().replace('-', '_')}_TOKEN"


def refresh_env(name: str) -> str:
    return f"WYND_MCP_{name.upper().replace('-', '_')}_REFRESH_TOKEN"


def oauth_start(registry: Registry, name: str, *, redirect_uri: str, return_to: str | None = None) -> str:
    """-> the authorize URL (pending state kept in memory for 10 minutes)."""
    entry = _entry(registry, name)
    if entry.transport != "http" or not entry.url:
        raise Invalid(f"MCP server {name!r} uses transport {entry.transport}; OAuth needs an http server")
    stored = entry.oauth or {}
    client_id = stored.get("client_id")
    with http_client() as client:
        metadata = _server_metadata(client, entry)
        registration = metadata.get("registration_endpoint")
        reusable = client_id and stored.get("redirect_uri", redirect_uri) == redirect_uri
        if isinstance(registration, str) and not reusable:
            client_id = _register(client, registration, redirect_uri, name)
    if not client_id:
        raise Invalid(f"MCP server {name!r}: its authorization server offers no dynamic client registration and no "
                      "client_id is stored",
                      hint="register a client with the provider and set oauth.client_id on the MCP entry")

    verifier = secrets.token_urlsafe(64)                     # 86 characters (RFC 7636: 43..128)
    state = secrets.token_urlsafe(24)
    pending = _Pending(name=name, client_id=client_id, verifier=verifier, redirect_uri=redirect_uri,
                       return_to=return_to, token_endpoint=metadata["token_endpoint"], resource=entry.url,
                       expires=now() + STATE_TTL)
    with _pending_lock:
        _drop_expired()
        _pending[state] = pending
    params = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
              "code_challenge": pkce_challenge(verifier), "code_challenge_method": "S256", "state": state,
              "resource": entry.url}
    endpoint = metadata["authorization_endpoint"]
    return f"{endpoint}{'&' if '?' in endpoint else '?'}{urlencode(params)}"


def oauth_complete(registry: Registry, state: str, code: str) -> tuple[McpServerEntry, str | None]:
    """-> (the updated `McpServerEntry`, return_to)."""
    with _pending_lock:
        _drop_expired()
        pending = _pending.pop(state, None)
    if pending is None:
        raise Invalid("unknown or expired OAuth state",
                      hint="start the OAuth flow again (a sign-in must finish within 10 minutes)")
    entry = _entry(registry, pending.name)
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": pending.redirect_uri,
            "client_id": pending.client_id, "code_verifier": pending.verifier, "resource": pending.resource}
    with http_client() as client:
        tokens = _token_request(client, pending.token_endpoint, form, entry.name)

    stored_refresh_env = _save_tokens(registry, entry.name, tokens)
    headers = {k: v for k, v in entry.headers.items() if k.lower() != "authorization"}
    headers["Authorization"] = f"Bearer ${{env:{token_env(entry.name)}}}"
    values = [*headers.values(), *entry.env.values()]
    auth_env = list(dict.fromkeys(ref for value in values for ref in ENV_REF.findall(value)))
    oauth = {"client_id": pending.client_id, "token_endpoint": pending.token_endpoint,
             "expires_at": _expires_at(tokens), "refresh_env": stored_refresh_env,
             "redirect_uri": pending.redirect_uri}
    updated = entry.model_copy(update={"headers": headers, "auth_env": auth_env, "oauth": oauth})
    registry.put(MCP, entry.name, updated.model_dump(mode="json"))
    return updated, pending.return_to


def refresh_tokens(registry: Registry) -> list[str]:
    """Refresh tokens expiring within 60 s; -> names refreshed. Failures are logged and skipped."""
    refreshed = []
    with _refresh_lock:                     # a rotated refresh token must not be spent twice
        deadline = now() + REFRESH_WITHIN
        due = [(name, raw) for name, raw in sorted(registry.list(MCP).items())
               if (expires := _parse_time((raw.get("oauth") or {}).get("expires_at"))) and expires <= deadline]
        if not due:
            return []
        stored = registry.secrets()
        with http_client() as client:
            for name, raw in due:
                try:
                    _refresh(registry, client, McpServerEntry.model_validate(raw), stored)
                except Exception as err:    # a failed refresh never stops the run, serve or tick that asked for it
                    log.warning("MCP server %r: OAuth token refresh failed: %s", name, err)
                    continue
                refreshed.append(name)
    return refreshed


def pkce_challenge(verifier: str) -> str:
    """RFC 7636 S256: BASE64URL(SHA256(verifier)) without padding."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _entry(registry: Registry, name: str) -> McpServerEntry:
    raw = registry.get(MCP, name)
    if raw is None:
        raise NotFound(f"unknown MCP server {name!r}", hint="add it first (wynd mcp add)")
    return McpServerEntry.model_validate(raw)


def _server_metadata(client: httpx.Client, entry: McpServerEntry) -> dict[str, Any]:
    """RFC 9728 -> RFC 8414. The protected-resource metadata names the issuer; without it the origin is the issuer."""
    parts = urlsplit(entry.url or "")
    origin = f"{parts.scheme}://{parts.netloc}"
    servers = _get_json(client, origin + RESOURCE_METADATA).get("authorization_servers")
    issuer = servers[0] if isinstance(servers, list) and servers and isinstance(servers[0], str) else origin
    issuer_parts = urlsplit(issuer)
    url = f"{issuer_parts.scheme}://{issuer_parts.netloc}{SERVER_METADATA}{issuer_parts.path.rstrip('/')}"
    metadata = _get_json(client, url)
    if not all(isinstance(metadata.get(key), str) for key in ("authorization_endpoint", "token_endpoint")):
        raise Unavailable(f"MCP server {entry.name!r}: no OAuth authorization server metadata at {url}",
                          hint="if the server does not support OAuth, register it with --auth-env instead")
    return metadata


def _register(client: httpx.Client, endpoint: str, redirect_uri: str, name: str) -> str:
    """RFC 7591 dynamic registration of a public client; -> client_id."""
    body = {"client_name": "Wynd", "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none"}
    try:
        response = client.post(endpoint, json=body)
    except httpx.HTTPError as err:
        raise Unavailable(f"MCP server {name!r}: client registration at {endpoint} failed: {err}") from err
    client_id = _json(response).get("client_id")
    if not response.is_success or not isinstance(client_id, str) or not client_id:
        raise Unavailable(f"MCP server {name!r}: client registration at {endpoint} answered "
                          f"{response.status_code}{_oauth_error(response)}")
    return client_id


def _token_request(client: httpx.Client, endpoint: str, form: dict[str, str], name: str) -> dict[str, Any]:
    try:
        response = client.post(endpoint, data=form)
    except httpx.HTTPError as err:
        raise Unavailable(f"MCP server {name!r}: token request to {endpoint} failed: {err}") from err
    tokens = _json(response)
    if response.is_success and isinstance(tokens.get("access_token"), str) and tokens["access_token"]:
        return tokens
    message = f"MCP server {name!r}: token endpoint {endpoint} answered {response.status_code}{_oauth_error(response)}"
    if response.status_code >= 500:
        raise Unavailable(message)
    raise Invalid(message)


def _refresh(registry: Registry, client: httpx.Client, entry: McpServerEntry, stored: dict[str, str]) -> None:
    oauth = entry.oauth or {}
    refresh_token = stored.get(oauth.get("refresh_env") or "")
    if not refresh_token:
        raise Invalid("no refresh token is stored; run the OAuth flow again")
    form = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": oauth["client_id"],
            "resource": entry.url or ""}
    tokens = _token_request(client, oauth["token_endpoint"], form, entry.name)
    rotated_env = _save_tokens(registry, entry.name, tokens)
    updated = {**oauth, "expires_at": _expires_at(tokens), "refresh_env": rotated_env or oauth["refresh_env"]}
    registry.put(MCP, entry.name, entry.model_copy(update={"oauth": updated}).model_dump(mode="json"))


def _save_tokens(registry: Registry, name: str, tokens: dict[str, Any]) -> str | None:
    """Store the access token (and the refresh token when one came); -> the refresh token's env name, if stored."""
    registry.put_secret(token_env(name), tokens["access_token"])
    refresh_token = tokens.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        return None
    registry.put_secret(refresh_env(name), refresh_token)
    return refresh_env(name)


def _expires_at(tokens: dict[str, Any]) -> str | None:
    seconds = tokens.get("expires_in")
    if not isinstance(seconds, int | float) or isinstance(seconds, bool):
        return None
    return (now() + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _drop_expired() -> None:
    """Caller holds `_pending_lock`."""
    cutoff = now()
    for state in [state for state, pending in _pending.items() if pending.expires <= cutoff]:
        del _pending[state]


def _get_json(client: httpx.Client, url: str) -> dict[str, Any]:
    """The JSON object at `url`; {} on any network, status or format failure."""
    try:
        response = client.get(url)
    except httpx.HTTPError:
        return {}
    return _json(response) if response.is_success else {}


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _oauth_error(response: httpx.Response) -> str:
    """` (error: description)` from an RFC 6749 §5.2 error body, or ''."""
    data = _json(response)
    detail = ": ".join(str(data[key]) for key in ("error", "error_description") if data.get(key))
    return f" ({detail})" if detail else ""

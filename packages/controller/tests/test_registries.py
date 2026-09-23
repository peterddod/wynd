"""`RegistryService` (PLAN §8.1 registries row, §3.14; `$DRAFTS/06 §5.13`) over the real file registry in the
test's `WYND_HOME`."""

from __future__ import annotations

import json

import pytest

import wynd.controller.oauth as oauth
from wynd.controller.errors import Invalid
from wynd.process.artefacts import ImageRegistryEntry
from wynd.runtime.mcp.entry import McpServerEntry
from wynd.runtime.providers import available_providers, provider_info


@pytest.fixture
def registries(controller):
    return controller.registries


def http_entry(name: str = "github", **kw) -> McpServerEntry:
    return McpServerEntry(name=name, transport="http", url="https://api.githubcopilot.com/mcp/", **kw)


# --- MCP servers ----------------------------------------------------------------------------------------------------

def test_mcp_add_list_upsert(registries, controller):
    entry = http_entry(headers={"Authorization": "Bearer ${env:GITHUB_TOKEN}", "X-Org": "${env:GH_ORG}"})
    stored = registries.add_mcp(entry)
    assert stored.auth_env == ["GITHUB_TOKEN", "GH_ORG"]                      # every referenced env var is listed
    stdio = McpServerEntry(name="local-files", transport="stdio", command=["mcp-files", "--root", "."],
                           env={"TOKEN": "${env:FILES_TOKEN}"}, auth_env=["FILES_TOKEN"])
    registries.add_mcp(stdio)
    assert [e.name for e in registries.list_mcp()] == ["github", "local-files"]
    assert registries.list_mcp()[0] == stored

    registries.add_mcp(http_entry(description="updated"))                    # upsert
    assert [(e.name, e.description) for e in registries.list_mcp()] == [("github", "updated"), ("local-files", "")]
    on_disk = json.loads((controller.ctx.stores.registry.home / "mcp.json").read_text())
    assert on_disk["entries"]["github"]["url"] == "https://api.githubcopilot.com/mcp/"


@pytest.mark.parametrize(("entry", "match"), [
    (McpServerEntry(name="x", transport="http", url="ftp://host/mcp"), "http\\(s\\) URL"),
    (McpServerEntry(name="x", transport="http", url="localhost:8080"), "http\\(s\\) URL"),
    (McpServerEntry(name="x", transport="http", url="https://h/mcp", auth_env=["bad-name"]), "invalid env var"),
])
def test_mcp_add_validates_at_the_boundary(registries, entry, match):
    with pytest.raises(Invalid, match=match):
        registries.add_mcp(entry)
    assert registries.list_mcp() == []


def test_mcp_remove_reports_referencing_processes(registries, workspace, commit, agentic_files):
    commit(workspace, "p4", agentic_files("p4", mcp="github"))
    registries.add_mcp(http_entry())
    result = registries.remove_mcp("github")
    assert result.model_dump() == {"removed": True, "referenced_by": ["p4"]}
    assert registries.list_mcp() == []
    assert registries.remove_mcp("github").model_dump() == {"removed": False, "referenced_by": ["p4"]}


# --- providers ------------------------------------------------------------------------------------------------------

def test_list_providers_covers_every_installed_provider(registries):
    providers = {p.name: p for p in registries.list_providers()}
    assert set(providers) == set(available_providers())
    fake = providers["fake"]
    assert (fake.kind, fake.ready, fake.tiers) == ("agent", True, provider_info("fake").default_tiers)
    assert providers["anthropic"].kind == "model"


def test_add_provider_overlays_only_the_given_tiers(registries, controller):
    first = registries.add_provider("fake", tiers={"cheap": "fake-small"})
    assert first.tiers == {**provider_info("fake").default_tiers, "cheap": "fake-small"}
    second = registries.add_provider("fake", tiers={"strong": "fake-large"})
    assert second.tiers == {"cheap": "fake-small", "standard": provider_info("fake").default_tiers["standard"],
                            "strong": "fake-large"}
    assert controller.ctx.stores.registry.get("providers", "fake") == {
        "tiers": {"cheap": "fake-small", "strong": "fake-large"}}           # only the user's overlay is stored


@pytest.mark.parametrize(("name", "tiers", "match"), [
    ("nope", {}, "unknown provider 'nope' \\(installed: .*fake"),
    ("fake", {"huge": "x"}, "unknown tier\\(s\\) huge"),
    ("fake", {"cheap": " "}, "need a model id"),
])
def test_add_provider_validates(registries, controller, name, tiers, match):
    with pytest.raises(Invalid, match=match):
        registries.add_provider(name, tiers=tiers)
    assert controller.ctx.stores.registry.list("providers") == {}


def test_add_provider_requires_all_three_tiers_after_the_merge(registries, controller, monkeypatch):
    import wynd.runtime.providers as providers

    class Partial:
        name, kind, default_tiers = "partial", "model", {"cheap": "p-small"}
        env_fragment = provider_info("fake").env_fragment

    undo = providers.register_for_tests("partial", Partial)
    try:
        with pytest.raises(Invalid, match="needs a model for every tier .* missing standard, strong"):
            registries.add_provider("partial", tiers={"cheap": "p-tiny"})
        done = registries.add_provider("partial", tiers={"standard": "p-mid", "strong": "p-big"})
        assert done.tiers == {"cheap": "p-small", "standard": "p-mid", "strong": "p-big"}
    finally:
        undo()


def test_remove_provider_reports_referencing_processes(registries, workspace, commit, agentic_files):
    commit(workspace, "p4", agentic_files("p4", provider="anthropic"))
    registries.add_provider("anthropic", tiers={"cheap": "claude-haiku-4-5"})
    assert registries.remove_provider("anthropic").model_dump() == {"removed": True, "referenced_by": ["p4"]}
    assert registries.remove_provider("fake").model_dump() == {"removed": False,
                                                              "referenced_by": ["p1", "p2", "p4", "parent"]}


# --- image registries -----------------------------------------------------------------------------------------------

def test_image_registries_have_at_most_one_default(registries):
    registries.add_image_registry(ImageRegistryEntry(name="local", url="localhost:5001/wynd", insecure=True,
                                                     default=True))
    registries.add_image_registry(ImageRegistryEntry(name="ghcr", url="ghcr.io/peterddod", password_env="GH_PAT"))
    assert registries.default_image_registry().name == "local"
    registries.add_image_registry(ImageRegistryEntry(name="ghcr", url="ghcr.io/peterddod", password_env="GH_PAT",
                                                     default=True))
    assert [(e.name, e.default) for e in registries.list_image_registries()] == [("ghcr", True), ("local", False)]
    assert registries.default_image_registry().name == "ghcr"
    assert registries.remove_image_registry("ghcr").removed is True
    assert registries.default_image_registry() is None
    assert registries.remove_image_registry("ghcr").removed is False


@pytest.mark.parametrize("entry", [ImageRegistryEntry(name="x", url=" "),
                                   ImageRegistryEntry(name="x", url="r.io/x", username_env="not valid")])
def test_image_registry_validation(registries, entry):
    with pytest.raises(Invalid):
        registries.add_image_registry(entry)


# --- OAuth delegation -----------------------------------------------------------------------------------------------

def test_refresh_tokens_only_calls_oauth_for_oauth_entries(registries, monkeypatch):
    calls = []
    monkeypatch.setattr(oauth, "refresh_tokens", lambda registry: calls.append(registry) or ["linear"])
    registries.add_mcp(http_entry())
    assert registries.refresh_tokens() == [] and calls == []
    registries.add_mcp(http_entry("linear", oauth={"client_id": "c", "token_endpoint": "https://l/token"}))
    assert registries.refresh_tokens() == ["linear"] and calls == [registries.registry]


def test_oauth_start_and_complete_delegate(registries, monkeypatch):
    monkeypatch.setattr(oauth, "oauth_start", lambda registry, name, *, redirect_uri, return_to=None:
                        f"https://auth/{name}?r={redirect_uri}&t={return_to}")
    monkeypatch.setattr(oauth, "oauth_complete", lambda registry, state, code: (http_entry(), f"{state}/{code}"))
    assert registries.oauth_start("github", redirect_uri="http://cb", return_to="/x") == \
        "https://auth/github?r=http://cb&t=/x"
    assert registries.oauth_complete("s1", "c1") == (http_entry(), "s1/c1")

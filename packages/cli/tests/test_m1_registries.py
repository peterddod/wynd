"""`wynd mcp|provider|registry add|list|remove` round trips (PLAN §9, §3.22, §3.14; `$DRAFTS/06 §9.2`) through a
real `RegistryService` over the file user registry (`WYND_HOME` is a per-test directory)."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def ctl(workspace, make_controller, use_controller):
    return use_controller(make_controller(workspace))


def stored(ctl, section: str, name: str) -> dict | None:
    return ctl.ctx.stores.registry.get(section, name)


def test_the_controller_exports_the_entry_models_the_cli_builds():
    from wynd.controller import models
    from wynd.process.artefacts import ImageRegistryEntry
    from wynd.runtime.mcp.entry import McpServerEntry

    assert (getattr(models, "McpServerEntry", None), getattr(models, "ImageRegistryEntry", None)) == (
        McpServerEntry, ImageRegistryEntry)
    assert {"McpServerEntry", "ImageRegistryEntry"} <= set(models.__all__)


@pytest.mark.parametrize(("model", "args"), [
    ("McpServerEntry", ["mcp", "add", "x", "--url", "http://h/mcp"]),
    ("ImageRegistryEntry", ["registry", "add", "x", "h/x"]),
])
def test_add_with_a_controller_lacking_the_entry_model_is_unavailable(ctl, cli, monkeypatch, model, args):
    from wynd.controller import models

    monkeypatch.delattr(models, model, raising=False)
    result = cli(*args, code=3)
    assert result.stderr == (f"error: the installed wynd-controller does not export wynd.controller.models.{model}; "
                             "upgrade wynd-controller\n")


# --- MCP servers ------------------------------------------------------------------------------------------------------

def test_mcp_http_server_with_auth_env(ctl, cli):
    result = cli("mcp", "add", "github", "--url", "https://api.githubcopilot.com/mcp/", "--auth-env", "GITHUB_TOKEN")
    assert result.stdout == "added MCP server github (http https://api.githubcopilot.com/mcp/)\n"
    entry = stored(ctl, "mcp", "github")
    assert (entry["transport"], entry["url"], entry["headers"], entry["auth_env"]) == (
        "http", "https://api.githubcopilot.com/mcp/", {"Authorization": "Bearer ${env:GITHUB_TOKEN}"},
        ["GITHUB_TOKEN"])

    cli("mcp", "add", "search", "--url", "http://localhost:9000/mcp", "--auth-env", "SEARCH_KEY",
        "--header", "Authorization=Token ${env:SEARCH_KEY}", "--header", "X-Team=${env:TEAM_ID}")
    entry = stored(ctl, "mcp", "search")
    assert entry["headers"] == {"Authorization": "Token ${env:SEARCH_KEY}", "X-Team": "${env:TEAM_ID}"}
    assert entry["auth_env"] == ["SEARCH_KEY", "TEAM_ID"]

    lines = cli("mcp", "list").stdout.splitlines()
    assert [line.split() for line in lines] == [
        ["NAME", "TRANSPORT", "URL", "AUTH"],
        ["github", "http", "https://api.githubcopilot.com/mcp/", "env:GITHUB_TOKEN"],
        ["search", "http", "http://localhost:9000/mcp", "env:SEARCH_KEY,", "env:TEAM_ID"],
    ]
    items = json.loads(cli("mcp", "list", "--json").stdout)["items"]
    assert [item["name"] for item in items] == ["github", "search"]


def test_mcp_stdio_server(ctl, cli):
    doc = json.loads(cli("mcp", "add", "files", "--command", "npx -y @acme/files-mcp --root '/data dir'",
                         "--auth-env", "FILES_TOKEN", "--json").stdout)
    assert (doc["transport"], doc["command"], doc["env"], doc["auth_env"]) == (
        "stdio", ["npx", "-y", "@acme/files-mcp", "--root", "/data dir"], {"FILES_TOKEN": "${env:FILES_TOKEN}"},
        ["FILES_TOKEN"])
    row = cli("mcp", "list").stdout.splitlines()[1]
    assert row.split()[:3] == ["files", "stdio", "npx"] and "'/data dir'" in row


@pytest.mark.parametrize(("args", "message"), [
    (["mcp", "add", "x"], "give exactly one of --url (http server) or --command (stdio server)"),
    (["mcp", "add", "x", "--url", "http://h/mcp", "--command", "srv"], "give exactly one of --url"),
    (["mcp", "add", "x", "--command", "srv", "--header", "A=b"], "--header applies to --url servers only"),
    (["mcp", "add", "GitHub", "--url", "http://h/mcp"], "MCP server name 'GitHub' must match"),
    (["mcp", "add", "x", "--url", "ftp://h/mcp"], "url must be an http(s) URL"),
    (["mcp", "add", "x", "--url", "http://h/mcp", "--header", "no-equals"], "--header expects NAME=VALUE"),
    (["mcp", "add", "x", "--url", "http://h/mcp", "--auth-env", "BAD-NAME"], "invalid env var name(s) BAD-NAME"),
])
def test_mcp_add_refusals_exit_2(ctl, cli, args, message):
    assert message in cli(*args, code=2).stderr
    assert ctl.ctx.stores.registry.list("mcp") == {}


def test_mcp_remove(ctl, cli):
    cli("mcp", "add", "github", "--url", "https://h/mcp")
    assert cli("mcp", "remove", "github").stdout == "removed MCP server github\n"
    assert stored(ctl, "mcp", "github") is None
    assert "no MCP server 'github' in the user registry" in cli("mcp", "remove", "github", code=3).stderr


# --- providers --------------------------------------------------------------------------------------------------------

def test_provider_tiers_round_trip(ctl, cli):
    result = cli("provider", "add", "fake", "--tier", "cheap=fake-small")
    assert result.stdout == "fake (agent): cheap=fake-small, standard=fake, strong=fake\n"
    assert "re-record with `wynd test --live`" in result.stderr
    assert stored(ctl, "providers", "fake") == {"tiers": {"cheap": "fake-small"}}
    cli("provider", "add", "fake", "--tier", "strong=fake-large")                 # only the given tiers change
    assert stored(ctl, "providers", "fake") == {"tiers": {"cheap": "fake-small", "strong": "fake-large"}}

    rows = {line.split()[0]: line.split() for line in cli("provider", "list").stdout.splitlines()}
    assert rows["NAME"] == ["NAME", "KIND", "CHEAP", "STANDARD", "STRONG", "READY"]
    assert rows["fake"] == ["fake", "agent", "fake-small", "fake", "fake-large", "yes"]
    assert rows["claude-code"][:5] == ["claude-code", "agent", "haiku", "sonnet", "opus"]
    assert rows["anthropic"][:2] == ["anthropic", "model"]
    infos = {i["name"]: i for i in json.loads(cli("provider", "list", "--json").stdout)["items"]}
    assert infos["fake"]["tiers"] == {"cheap": "fake-small", "standard": "fake", "strong": "fake-large"}


def test_provider_remove_warns_about_processes_using_it(ctl, cli):
    cli("provider", "add", "fake", "--tier", "cheap=fake-small")
    result = cli("provider", "remove", "fake")
    assert result.stdout == "removed provider entry fake\n"
    assert result.stderr == "warning: still referenced by: p1, p2, p3, parent\n"
    assert stored(ctl, "providers", "fake") is None
    assert "no provider entry 'fake'" in cli("provider", "remove", "fake", code=3).stderr


@pytest.mark.parametrize(("args", "message"), [
    (["provider", "add", "nope", "--tier", "cheap=x"], "unknown provider 'nope'"),
    (["provider", "add", "fake", "--tier", "huge=x"], "unknown tier(s) huge"),
    (["provider", "add", "fake", "--tier", "cheap"], "--tier expects TIER=MODEL"),
    (["provider", "add", "fake", "--tier", "cheap= "], "tier(s) cheap need a model id"),
])
def test_provider_add_refusals_exit_2(ctl, cli, args, message):
    assert message in cli(*args, code=2).stderr
    assert ctl.ctx.stores.registry.list("providers") == {}


# --- image registries -------------------------------------------------------------------------------------------------

def test_image_registries_round_trip(ctl, cli):
    result = cli("registry", "add", "local", "localhost:5001/wynd", "--insecure", "--default")
    assert result.stdout == "added image registry local (localhost:5001/wynd) [default]\n"
    cli("registry", "add", "ghcr", "ghcr.io/acme", "--username-env", "GHCR_USER", "--password-env", "GHCR_TOKEN",
        "--default")
    assert stored(ctl, "registries", "local")["default"] is False                 # one default at a time
    assert [line.split() for line in cli("registry", "list").stdout.splitlines()] == [
        ["NAME", "URL", "DEFAULT", "INSECURE", "AUTH"],
        ["ghcr", "ghcr.io/acme", "yes", "-", "env:GHCR_USER", "env:GHCR_TOKEN"],
        ["local", "localhost:5001/wynd", "-", "yes", "-"],
    ]
    items = json.loads(cli("registry", "list", "--json").stdout)["items"]
    assert [(i["name"], i["default"]) for i in items] == [("ghcr", True), ("local", False)]

    assert cli("registry", "remove", "local").stdout == "removed image registry local\n"
    assert "no image registry 'local'" in cli("registry", "remove", "local", code=3).stderr
    assert "invalid env var name(s) bad-name" in cli("registry", "add", "x", "h/x", "--username-env", "bad-name",
                                                     code=2).stderr

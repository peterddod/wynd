"""Env manifest assembly and the user-registry snapshot (PLAN §3.10, §6.1 envmanifest row; `$DRAFTS/04 §10`)."""

from __future__ import annotations

import pytest
from support.proc_env_workspaces import (  # noqa: F401
    MemRegistry,
    fake_providers,
    lock_only,
    to_yaml,
    validate_double,
)

from wynd.process.envmanifest import assemble_env_manifest, registry_snapshot
from wynd.process.workspace import load_workspace
from wynd.runtime.storage import STORAGE_ENV
from wynd.runtime.supervisor.schema import SUPERVISOR_ENV
from wynd.spec.env_manifest import check_env
from wynd.spec.errors import SpecError
from wynd.spec.lockfiles import EdgeLockEntry, EdgesLock, check_hash, dump_lock

CHECK = "The ticket is complete."
GITHUB = {"name": "github", "transport": "http", "url": "https://api.githubcopilot.com/mcp/",
          "headers": {"Authorization": "Bearer ${env:GITHUB_TOKEN}"}, "auth_env": ["GITHUB_TOKEN"]}
TRIAGE = {
    "kind": "process", "name": "triage", "env": {"vars": {"QUEUE_DIR": "Where tickets are filed."}},
    "entry": "lookup", "inputs": {"issue": "string"}, "outputs": {"ticket": "string"},
    "steps": {"lookup": {"use": "./steps/lookup"}, "file": {"use": "./steps/file"}},
    "edges": [
        {"from": "lookup.done", "to": "file",
         "with": {"issue": "steps.lookup.outputs.summary", "dir": "env.QUEUE_DIR"}},
        {"from": "file.done", "kind": "agentic", "to": [
            {"step": "$exit.done", "check": CHECK, "with": {"ticket": "steps.file.outputs.ticket"}},
            {"step": "$exit.done", "with": {"ticket": '"incomplete"'}},
        ]},
    ],
}
LOOKUP = {
    "kind": "agentic", "tier": "cheap", "thinking": "low", "effects": ["network"],
    "tools": [{"name": "web_search", "source": "library", "effects": ["network"], "env": ["BRAVE_API_KEY"]}],
    "mcp": [{"server": "github", "allow": ["get_issue"],
             "tools": [{"name": "get_issue", "input_schema": {"type": "object"}}], "hash": "sha256:" + "0" * 64}],
    "fragment": {"vars": [
        {"name": "BRAVE_API_KEY", "description": "Brave Search API key.", "secret": True},
        {"name": "GITHUB_TOKEN", "description": "GitHub token."},                 # not marked secret here
    ]},
}
FILE = {"fragment": {"vars": [{"name": "TICKET_PREFIX", "description": "Prefix of ticket ids.", "required": False,
                               "default": "T-"}]}}


def triage_files(edges_lock: EdgesLock | None = None, **extra_lock) -> dict[str, str]:
    files = {
        "wynd.yaml": "process_roots: [processes]\n",
        "processes/triage/process.yaml": to_yaml(TRIAGE),
        **lock_only("processes/triage/steps/lookup", **LOOKUP),
        **lock_only("processes/triage/steps/file", **{**FILE, **extra_lock}),
    }
    if edges_lock is not None:
        files["processes/triage/edges.lock.yaml"] = dump_lock(edges_lock)
    return files


def registry() -> MemRegistry:
    return MemRegistry({"mcp": {"github": GITHUB},
                        "providers": {"claude-code": {"tiers": {"cheap": "sonnet"}},
                                      "anthropic": {"tiers": {"cheap": "claude-sonnet-5"}}}})


def var(name, description="", *, secret=False, required=True, default=None, one_of=None, used_by=()) -> dict:
    return {"name": name, "description": description, "secret": secret, "required": required, "default": default,
            "one_of": one_of, "used_by": sorted(used_by)}


CLAUDE_USERS = ["edge:triage:file.done[0]", "provider:claude-code", "step:triage#lookup"]
PROCESS_VARS = [
    var("ANTHROPIC_API_KEY", "Anthropic API key.", secret=True, required=False, one_of="claude-code-auth",
        used_by=CLAUDE_USERS),
    var("BRAVE_API_KEY", "Brave Search API key.", secret=True,
        used_by=["step:triage#lookup", "tool:triage#lookup/web_search"]),
    var("CLAUDE_CODE_OAUTH_TOKEN", "Claude Code token.", secret=True, required=False, one_of="claude-code-auth",
        used_by=CLAUDE_USERS),
    var("GITHUB_TOKEN", "GitHub token.", secret=True, used_by=["mcp:github", "step:triage#lookup"]),
    var("QUEUE_DIR", "Where tickets are filed.", used_by=["edge:triage:lookup.done[0].dir"]),
    var("TICKET_PREFIX", "Prefix of ticket ids.", required=False, default="T-", used_by=["step:triage#file"]),
    var("WYND_MCP_GITHUB_URL", "URL of MCP server github; overrides the user-registry url (e.g. an in-cluster "
        "address).", required=False, default="https://api.githubcopilot.com/mcp/", used_by=["mcp:github"]),
    var("WYND_REGISTRY_JSON", "User-registry snapshot. MCP: github (http https://api.githubcopilot.com/mcp/). "
        "Tiers: claude-code cheap=sonnet.", required=False, one_of="user-registry",
        used_by=["mcp:github", "provider:claude-code"]),
]


def runtime_vars() -> list[dict]:
    """STORAGE_ENV and SUPERVISOR_ENV as published by the runtime; WYND_HOME gains the manifest's `runtime` user."""
    out = []
    for item in [*STORAGE_ENV, *SUPERVISOR_ENV]:
        entry = item.model_dump(mode="json")
        if item.name == "WYND_HOME":
            entry["used_by"] = sorted({*item.used_by, "runtime"})
        out.append(entry)
    return out


def test_manifest_golden(make_repo, validate_double):
    ws = load_workspace(make_repo(files=triage_files()))
    manifest = assemble_env_manifest(ws, "triage", registry(), commit="abc123", providers=fake_providers)
    expected_vars = sorted([*PROCESS_VARS, *runtime_vars()], key=lambda v: v["name"])
    assert manifest.model_dump(mode="json") == {
        "wynd": 1, "process": "triage", "commit": "abc123",
        "vars": expected_vars,
        "groups": {
            "claude-code-auth": {"description": "One claude-code credential.", "modes": ["image"]},
            "user-registry": {"description": "The user-registry entries this process uses (MCP servers, provider "
                              "tiers), supplied by the controller for image runs; locally the file registry under "
                              "WYND_HOME is read instead.", "modes": ["image"]},
        },
    }
    names = [v.name for v in manifest.vars]
    assert names == sorted(names)
    assert {"WYND_RUN_API_TOKEN", "WYND_PORT", "WYND_DATA_DIR", "WYND_HOME"} <= set(names)


def test_check_env_modes_on_the_assembled_manifest(make_repo, validate_double):
    ws = load_workspace(make_repo(files=triage_files()))
    manifest = assemble_env_manifest(ws, "triage", registry(), providers=fake_providers)
    required = {"BRAVE_API_KEY": "b", "GITHUB_TOKEN": "g", "QUEUE_DIR": "/q"}

    local = check_env(manifest, required, mode="local")
    assert local.ok
    assert sorted((d.severity, d.code, d.loc) for d in local.diagnostics) == [
        ("warning", "W-ENV-ONE-OF", ("groups", "claude-code-auth")),
        ("warning", "W-ENV-ONE-OF", ("groups", "user-registry")),
    ]

    image = check_env(manifest, required, mode="image")
    assert not image.ok
    assert sorted(d.code for d in image.diagnostics) == ["E-ENV-ONE-OF", "E-ENV-ONE-OF"]
    assert check_env(manifest, {**required, "CLAUDE_CODE_OAUTH_TOKEN": "t", "WYND_REGISTRY_JSON": "{}"},
                     mode="image").ok

    missing = check_env(manifest, {**required, "QUEUE_DIR": ""}, mode="local")
    [error] = [d for d in missing.diagnostics if d.severity == "error"]
    assert error.code == "E-ENV-MISSING"
    assert "QUEUE_DIR" in error.message and "edge:triage:lookup.done[0].dir" in error.message


def test_registry_snapshot_holds_exactly_the_used_entries(make_repo, validate_double):
    lp = load_workspace(make_repo(files=triage_files())).load_process("triage")
    assert registry_snapshot(lp, registry()) == {"mcp": {"github": GITHUB},
                                                 "providers": {"claude-code": {"tiers": {"cheap": "sonnet"}}}}
    assert registry_snapshot(lp, MemRegistry()) == {}
    assert registry_snapshot(lp, MemRegistry({"providers": {"anthropic": {"tiers": {"cheap": "x"}}}})) == {}


def test_no_registry_entries_means_no_registry_var(make_repo, validate_double):
    ws = load_workspace(make_repo(files=triage_files()))
    empty = assemble_env_manifest(ws, "triage", MemRegistry(), providers=fake_providers)
    names = {v.name for v in empty.vars}
    assert "WYND_REGISTRY_JSON" not in names and "user-registry" not in empty.groups
    assert "WYND_MCP_GITHUB_URL" not in names                                  # the URL comes from the registry
    none = assemble_env_manifest(ws, "triage", None, providers=fake_providers)
    assert [v for v in none.vars if v.name == "GITHUB_TOKEN"][0].used_by == ["step:triage#lookup"]
    assert not [v for v in none.vars if v.name == "GITHUB_TOKEN"][0].secret


def test_an_edge_lock_provider_override_lists_that_providers_vars(make_repo, validate_double):
    lock = EdgesLock(edges={"file.done[0]": EdgeLockEntry(check_hash=check_hash(CHECK, None), provider="anthropic")})
    ws = load_workspace(make_repo(files=triage_files(edges_lock=lock)))
    manifest = assemble_env_manifest(ws, "triage", registry(), providers=fake_providers)
    by_name = {v.name: v for v in manifest.vars}
    assert by_name["CLAUDE_CODE_OAUTH_TOKEN"].used_by == ["provider:claude-code", "step:triage#lookup"]
    assert by_name["ANTHROPIC_API_KEY"].used_by == [
        "edge:triage:file.done[0]", "provider:anthropic", "provider:claude-code", "step:triage#lookup"]
    assert by_name["ANTHROPIC_API_KEY"].required                              # the anthropic fragment requires it
    assert "anthropic cheap=claude-sonnet-5" in by_name["WYND_REGISTRY_JSON"].description


def test_conflicting_defaults_are_an_error(make_repo, validate_double):
    port = {"fragment": {"vars": [{"name": "WYND_PORT", "required": False, "default": "9000"}]}}
    ws = load_workspace(make_repo(files=triage_files(**port)))
    with pytest.raises(SpecError) as err:
        assemble_env_manifest(ws, "triage", registry(), providers=fake_providers)
    assert [d.code for d in err.value.diagnostics] == ["E-ENV-CONFLICT"]

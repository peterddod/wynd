"""MCP discovery, snapshots and run-time verification (PLAN §3.6, §5.5; `$DRAFTS/03 §10.1, §10.4–§10.5`)."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from wynd.runtime.agentic.errors import McpConfigError, McpSnapshotMismatch
from wynd.runtime.errors import StepDefinitionError
from wynd.runtime.mcp import McpServer, McpServerEntry, list_tools, open_client, schema_sha256, snapshot, verify
from wynd.runtime.mcp.fake_server import DEFAULT_TOOLS, serve_http
from wynd.runtime.mcp.snapshot import McpToolSpec
from wynd.runtime.tools.toolset import ToolSet
from wynd.spec.hashing import hash_obj
from wynd.spec.lockfiles import McpSnapshot, McpToolSnapshot, StepLock

GITHUB = McpServer("github", allow=["list_issues", "get_issue"])
MISMATCH = """\
MCP server 'github' no longer matches the snapshot in step.lock.yaml:
  - get_issue: input schema changed
  - list_issues: missing on the server
Re-run `wynd compile` to re-snapshot, and review the diff."""


def entry(url: str, **kw: Any) -> McpServerEntry:
    return McpServerEntry(name="github", transport="http", url=url,
                          headers={"Authorization": "Bearer ${env:GITHUB_TOKEN}"}, auth_env=["GITHUB_TOKEN"], **kw)


def discovered() -> list[McpToolSpec]:
    with serve_http(token="gh") as server:
        return list_tools(entry(server.url), {"GITHUB_TOKEN": "gh"})


def block(snap: McpSnapshot, server_entry: McpServerEntry | None) -> dict[str, Any]:
    """An `ExecPolicy.mcp` item: McpSnapshot JSON + the user-registry entry."""
    return {**snap.model_dump(mode="json"), "entry": server_entry.model_dump(mode="json") if server_entry else None}


class Trace:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, type: str, **fields: Any) -> None:
        self.events.append({"type": type, **fields})


def toolset(tmp_path, *blocks: dict[str, Any]) -> ToolSet:
    rt = SimpleNamespace(run_id="run_1", workspace=tmp_path, trace=Trace(), http=None)
    return ToolSet([], runtime=rt, mcp=blocks)


def test_list_tools_reads_the_live_catalogue():
    tools = discovered()
    assert [t.name for t in tools] == [t["name"] for t in DEFAULT_TOOLS]
    assert tools[0] == McpToolSpec("get_issue", "Get a GitHub issue by number.", DEFAULT_TOOLS[0]["inputSchema"],
                                   {"readOnlyHint": True})
    assert tools[3].annotations == {} and tools[3].output_schema is None


def test_snapshot_keeps_only_allowed_tools_sorted_by_name():
    snap = snapshot(GITHUB, discovered())
    assert snap.server == "github" and snap.allow == ["list_issues", "get_issue"]
    assert snap.tools == [
        McpToolSnapshot(name="get_issue", description="Get a GitHub issue by number.",
                        input_schema=DEFAULT_TOOLS[0]["inputSchema"], idempotent=True),        # readOnlyHint
        McpToolSnapshot(name="list_issues", description="List the issues of the repository.",
                        input_schema=DEFAULT_TOOLS[1]["inputSchema"], idempotent=True),        # idempotentHint
    ]
    assert snap.hash == hash_obj([t.model_dump(mode="json") for t in snap.tools])
    not_idempotent = snapshot(McpServer("github", allow=["delete_repo"]), discovered())
    assert not_idempotent.tools[0].idempotent is False                                         # destructiveHint only


def test_a_snapshot_is_a_valid_lock_block():
    snap = snapshot(GITHUB, discovered())
    lock = StepLock(name="triage", kind="agentic", entrypoint="triage:Triage", effects=["network"], mcp=[snap])
    assert StepLock.model_validate(lock.model_dump(mode="json")).mcp == [snap]


def test_snapshot_refuses_an_allowed_tool_the_server_lacks():
    with pytest.raises(ValueError) as info:
        snapshot(McpServer("github", allow=["get_issue", "merge_pr"]), discovered())
    assert str(info.value) == (
        "MCP server 'github' has no tool 'merge_pr'; available: ['crash', 'delete_repo', 'get_issue', 'list_issues']"
    )


def test_schema_sha256_is_stable_under_key_order():
    a = {"type": "object", "properties": {"number": {"type": "integer"}, "repo": {"type": "string"}},
         "required": ["number"]}
    b = {"required": ["number"], "properties": {"repo": {"type": "string"}, "number": {"type": "integer"}},
         "type": "object"}
    assert schema_sha256("get_issue", a) == schema_sha256("get_issue", b)
    assert schema_sha256("get_issue", a).startswith("sha256:") and len(schema_sha256("get_issue", a)) == 71
    assert schema_sha256("get_issue", a) != schema_sha256("get_pr", a)
    assert schema_sha256("get_issue", a) != schema_sha256("get_issue", {**a, "required": ["number", "repo"]})


def test_verify_accepts_the_same_schemas_and_ignores_descriptions():
    snap = snapshot(GITHUB, discovered())
    tools = copy.deepcopy(DEFAULT_TOOLS)
    tools[0]["description"] = "A reworded description."
    tools[0]["inputSchema"] = dict(reversed(list(tools[0]["inputSchema"].items())))
    with serve_http(tools, token="gh") as server:
        client = open_client(entry(server.url), {"GITHUB_TOKEN": "gh"})
        try:
            verify(snap, client)
        finally:
            client.close()


def changed_server_tools() -> list[dict[str, Any]]:
    tools = copy.deepcopy(DEFAULT_TOOLS)
    tools[0]["inputSchema"]["properties"]["number"] = {"type": "string"}      # get_issue changed
    del tools[1]                                                             # list_issues gone
    return tools


def test_verify_names_every_problem():
    snap = snapshot(GITHUB, discovered())
    with serve_http(changed_server_tools(), token="gh") as server:
        client = open_client(entry(server.url), {"GITHUB_TOKEN": "gh"})
        try:
            with pytest.raises(McpSnapshotMismatch) as info:
                verify(snap, client)
        finally:
            client.close()
    assert str(info.value) == MISMATCH


def test_connect_mcp_verifies_every_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh")
    snap = snapshot(GITHUB, discovered())
    with serve_http(changed_server_tools(), token="gh") as server:
        tools = toolset(tmp_path, block(snap, entry(server.url)))
        with pytest.raises(McpSnapshotMismatch) as info:
            tools.connect_mcp()
        tools.close()
        assert [m for m, _, _ in server.requests][-1] == "DELETE"            # the failed connection was closed
    assert str(info.value) == MISMATCH


def test_connect_mcp_resolves_env_references_at_run_time(tmp_path, monkeypatch):
    snap = snapshot(GITHUB, discovered())
    with serve_http(token="gh") as server:
        tools = toolset(tmp_path, block(snap, entry(server.url)))
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        with pytest.raises(McpConfigError) as info:
            tools.connect_mcp()
        assert str(info.value) == "MCP server 'github' needs env var GITHUB_TOKEN (listed in process.env.yaml)"
        monkeypatch.setenv("GITHUB_TOKEN", "wrong")
        with pytest.raises(McpConfigError, match=r"rejected the credentials \(HTTP 401\) \(check GITHUB_TOKEN\)"):
            tools.connect_mcp()
        monkeypatch.setenv("GITHUB_TOKEN", "gh")
        tools.connect_mcp()
        tools.close()


def test_connect_mcp_needs_a_registry_entry(tmp_path):
    tools = toolset(tmp_path, block(snapshot(GITHUB, discovered()), None))
    with pytest.raises(McpConfigError, match="MCP server 'github' is not in the user registry"):
        tools.connect_mcp()


def test_the_model_sees_the_snapshot_not_the_live_server(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "gh")
    snap = snapshot(GITHUB, discovered())
    live = copy.deepcopy(DEFAULT_TOOLS)
    live[0]["description"] = "Live description that must not reach the model."
    with serve_http(live, token="gh") as server:
        tools = toolset(tmp_path, block(snap, entry(server.url)))
        tools.connect_mcp()
        try:
            schemas = {s.name: s for s in tools.schemas()}
            assert schemas["github__get_issue"].description == "Get a GitHub issue by number."
            assert schemas["github__get_issue"].input_schema == DEFAULT_TOOLS[0]["inputSchema"]
            assert set(schemas) == {"github__get_issue", "github__list_issues"}        # never delete_repo
            assert '"number": 7' in tools.invoke("github__get_issue", {"number": 7}).text
        finally:
            tools.close()


def test_mcp_server_declaration():
    server = McpServer("github", allow=["get_issue", "list_issues"])
    assert server == McpServer("github", allow=("get_issue", "list_issues"))
    assert server.allow == ("get_issue", "list_issues")
    with pytest.raises(StepDefinitionError, match="must name at least one tool"):
        McpServer("github", allow=[])
    with pytest.raises(StepDefinitionError, match="allow must be a list of tool names"):
        McpServer("github", allow="get_issue")
    with pytest.raises(TypeError):
        McpServer("github", ["get_issue"])                    # allow is keyword-only
    with pytest.raises(AttributeError):
        server.name = "other"

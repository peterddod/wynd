"""tools: catalog building, discovery failures, used MCP tools from events, allow narrowing, lock snapshots."""

import json

import pytest

from wynd.compiler.astcheck import StaticReport, ToolMethodInfo
from wynd.compiler.tools import BuiltinToolInfo, build_catalog, builtin_catalog, lock_tools, narrow_allow, used_tools
from wynd.runtime.mcp.snapshot import McpToolSpec
from wynd.runtime.tools import BUILTINS, tool, tool_spec
from wynd.spec.hashing import hash_obj
from wynd.spec.lockfiles import McpSnapshot, McpToolSnapshot, ToolSnapshot


@tool(effects=["network"], idempotent=True, env=["LOOKUP_KEY"])
def lookup(key: str, limit: int = 3) -> str:
    """Look up a key."""
    return key


@tool
def clock() -> str:
    """The time."""
    return ""


FAKE_BUILTINS = [tool_spec(lookup, "builtin"), tool_spec(clock, "builtin")]


class FakeRegistry:
    def __init__(self, mcp: dict):
        self.mcp = mcp

    def list(self, section: str) -> dict:
        assert section == "mcp"
        return self.mcp


GITHUB = {"transport": "http", "url": "https://github.invalid/mcp", "auth_env": ["GITHUB_TOKEN"],
          "description": "GitHub issues"}
TOOLS = [
    McpToolSpec("list_issues", "List issues", {"type": "object", "properties": {"repo": {"type": "string"}}},
                {"readOnlyHint": True}),
    McpToolSpec("get_issue", "Get one issue", {"type": "object"}, {}),
    McpToolSpec("close_issue", "Close an issue", {"type": "object"}, {"idempotentHint": True}),
]


def discover(entry):
    if entry.name == "broken":
        raise ConnectionError("connection refused")
    return list(TOOLS)


def catalog():
    registry = FakeRegistry({"github": GITHUB, "broken": {"transport": "http", "url": "http://127.0.0.1:9/mcp"}})
    return build_catalog(registry, discover, builtins=FAKE_BUILTINS)


def test_catalog_builtins_and_discovery_failure():
    cat = catalog()
    assert [b.name for b in cat.builtins] == ["lookup", "clock"]
    lookup_info = cat.builtin("lookup")
    assert lookup_info == BuiltinToolInfo("lookup", "lookup(key: string, limit: integer = 3)", "Look up a key.",
                                          ["network"], True, ["LOOKUP_KEY"])
    assert lookup_info.line() == ("- lookup(key: string, limit: integer = 3) — Look up a key. "
                                  "[effects: network; idempotent; env: LOOKUP_KEY]")
    assert cat.builtin("clock").line() == "- clock() — The time. [effects: none]"
    assert [s.name for s in cat.mcp] == ["github"]
    github = cat.server("github")
    assert github.entry.auth_env == ["GITHUB_TOKEN"]
    assert [t.name for t in github.tools] == ["close_issue", "get_issue", "list_issues"]
    assert github.summary() == {"description": "GitHub issues", "tools": {
        "close_issue": "Close an issue", "get_issue": "Get one issue", "list_issues": "List issues"}}
    assert len(cat.warnings) == 1 and "broken" in cat.warnings[0] and "connection refused" in cat.warnings[0]


def test_invalid_registry_entry_is_dropped_with_warning():
    cat = build_catalog(FakeRegistry({"bad": {"transport": "http"}}), discover, builtins=[])
    assert cat.mcp == [] and "bad" in cat.warnings[0]


def test_default_builtins_are_the_runtime_library():
    assert [b.name for b in builtin_catalog().builtins] == [s.name for s in BUILTINS]
    shell = builtin_catalog().builtin("shell")
    assert "shell.allow" in shell.line()


def static(**kwargs) -> StaticReport:
    base = {"errors": [], "env_vars": set(), "tools": [], "mcp": [], "context": [], "tool_methods": []}
    return StaticReport(**{**base, **kwargs})


def test_lock_tools_snapshots():
    cat = build_catalog(FakeRegistry({"github": GITHUB}), discover, builtins=[*FAKE_BUILTINS, *BUILTINS])
    report = static(
        tools=["lookup", "shell"], shell_allow=["pdftotext"],
        mcp=[("github", ["list_issues", "close_issue"])],
        tool_methods=[ToolMethodInfo("crm", ["network"], False, ["CRM_TOKEN"])],
    )
    tools, mcp = lock_tools(report, cat)
    assert tools == [
        ToolSnapshot(name="crm", source="method", effects=["network"], env=["CRM_TOKEN"]),
        ToolSnapshot(name="lookup", source="library", effects=["network"], idempotent=True, env=["LOOKUP_KEY"]),
        ToolSnapshot(name="shell", source="library", effects=["shell"], allow=["pdftotext"]),
    ]
    [snap] = mcp
    assert snap.server == "github" and snap.allow == ["list_issues", "close_issue"]
    assert [t.name for t in snap.tools] == ["close_issue", "list_issues"]
    assert [t.idempotent for t in snap.tools] == [True, True]
    assert snap.hash == hash_obj([t.model_dump(mode="json") for t in snap.tools])


def test_lock_tools_unknown():
    with pytest.raises(ValueError, match="unknown builtin tool 'nope'"):
        lock_tools(static(tools=["nope"]), catalog())
    with pytest.raises(ValueError, match="unknown MCP server 'jira'"):
        lock_tools(static(mcp=[("jira", ["x"])]), catalog())


def test_used_tools_from_events(tmp_path):
    events = tmp_path / "events.jsonl"
    lines = [
        {"type": "model.call", "step": "classify"},
        {"type": "tool.call", "tool": "list_issues", "source": "mcp:github"},
        {"type": "tool.call", "tool": "lookup", "source": "builtin"},
        {"type": "tool.call", "tool": "get_issue", "source": "mcp:github"},
        {"type": "tool.call", "tool": "list_issues", "source": "mcp:github"},
    ]
    events.write_text("\n".join(json.dumps(e) for e in lines) + "\n\n")
    assert used_tools(events) == {("github", "list_issues"), ("github", "get_issue")}
    assert used_tools(tmp_path / "missing.jsonl") == set()


def snap(server: str, names: list[str]) -> McpSnapshot:
    tools = [McpToolSnapshot(name=n, input_schema={"type": "object"}) for n in sorted(names)]
    return McpSnapshot(server=server, allow=names, tools=tools, hash=hash_obj([t.model_dump(mode="json")
                                                                              for t in tools]))


def test_narrow_allow():
    github, jira = snap("github", ["list_issues", "get_issue", "close_issue"]), snap("jira", ["search"])
    assert narrow_allow([github, jira], {("github", n) for n in github.allow} | {("jira", "search")}) is None
    narrowed = narrow_allow([github, jira], {("github", "get_issue"), ("github", "list_issues")})
    assert [s.server for s in narrowed] == ["github"]                    # nothing used on jira: dropped
    [only] = narrowed
    assert only.allow == ["list_issues", "get_issue"]
    assert [t.name for t in only.tools] == ["get_issue", "list_issues"]
    assert only.hash == hash_obj([t.model_dump(mode="json") for t in only.tools]) != github.hash
    assert narrow_allow([], set()) is None

"""Cassette keys and normalisation (PLAN §3.16, §15 item 39; `$DRAFTS/03 §11.2`)."""

import dataclasses
import hashlib
import os
from pathlib import Path

from wynd.runtime.cassettes.key import (
    DATETIME_RE,
    Normaliser,
    agent_request,
    continue_request,
    generate_request,
    request_key,
    tool_request,
)
from wynd.runtime.providers.types import AgentRequest, GenerateRequest, McpServerRef, ToolHandle, ToolResult, ToolSchema
from wynd.spec.hashing import canonical_json

NO_LITERALS = Normaliser({})


def _gen(**changes) -> GenerateRequest:
    req = GenerateRequest(
        model_id="claude-haiku-4-5",
        thinking="low",
        system="Extract the invoice fields.",
        messages=[{"role": "user", "content": [{"type": "text", "text": "INVOICE INV-1042"}]}],
        tools=[ToolSchema("lookup_rate", "Look up a rate.",
                          {"type": "object", "properties": {"c": {"type": "string"}}})],
        output_schema={"type": "object", "properties": {"exit": {"const": "done"}}},
    )
    return dataclasses.replace(req, **changes)


def _agent(workspace: Path, **changes) -> AgentRequest:
    req = AgentRequest(
        model_id="haiku",
        thinking="low",
        instruction="Extract the invoice fields.",
        context={"process.goal": "Pay supplier invoices"},
        input={"invoice_text": "INVOICE INV-1042"},
        output_schema={"type": "object"},
        tools=[ToolHandle("write_note", "Write a note.", {"type": "object"}, lambda a: ToolResult("ok"), local=True)],
        mcp_servers=[McpServerRef("github", ("get_issue",))],
        workspace=workspace,
    )
    return dataclasses.replace(req, **changes)


def test_key_is_sha256_of_the_normalised_canonical_json():
    canonical = generate_request(_gen(), provider="anthropic", tier="cheap")
    key = request_key(canonical, NO_LITERALS)
    assert key == hashlib.sha256(canonical_json(canonical)).hexdigest()
    assert len(key) == 64


def test_key_is_stable_under_dict_order():
    a = tool_request("fetch", {"url": "https://x.test", "headers": {"A": "1", "B": "2"}})
    b = tool_request("fetch", {"headers": {"B": "2", "A": "1"}, "url": "https://x.test"})
    assert request_key(a, NO_LITERALS) == request_key(b, NO_LITERALS)


def test_normaliser_replaces_workspace_its_realpath_run_id_and_datetimes(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    norm = Normaliser.for_run("run_20260922T215001100_a1b2c3", link)
    text = (
        f"{link}/a.txt {os.path.realpath(link)}/b.txt run_20260922T215001100_a1b2c3 "
        "2026-09-22T21:50:01Z 2026-09-22T21:50:01.123456+00:00 2026-09-22T21:50:01-05:00 "
        "due 2026-10-01 naive 2026-09-22T21:50:01"
    )
    assert norm.apply(text) == (
        "<workspace>/a.txt <workspace>/b.txt <run.id> <datetime> <datetime> <datetime> "
        "due 2026-10-01 naive 2026-09-22T21:50:01"
    )


def test_datetime_re_matches_the_now_builtin_format():
    assert DATETIME_RE.fullmatch("2026-09-22T21:50:01+00:00")
    assert not DATETIME_RE.fullmatch("2026-09-22")


def test_longest_literal_wins(tmp_path):
    ws_root = tmp_path / "ws"
    workspace = ws_root / ".wynd" / "workspaces" / "run_x"
    norm = Normaliser.for_run("run_x", workspace, {str(ws_root): "<ws>"})
    text = f"{workspace}/out.txt {ws_root}/processes/p run_x"
    assert norm.apply(text) == "<workspace>/out.txt <ws>/processes/p <run.id>"


def test_extra_literals_include_the_realpath_of_absolute_paths(tmp_path):
    real = tmp_path / "real-tmp"
    real.mkdir()
    link = tmp_path / "tmp-link"
    link.symlink_to(real)
    norm = Normaliser.for_run("r1", tmp_path / "workspace", {str(link): "<tmp>", "token-abc": "<token>"})
    assert norm.apply(f"{link}/x {os.path.realpath(link)}/y token-abc") == "<tmp>/x <tmp>/y <token>"


def test_empty_literals_are_ignored():
    assert Normaliser({"": "<empty>", "a": "<a>"}).apply("abc") == "<a>bc"


def test_restore_writes_the_replaying_runs_values_and_keeps_datetimes(tmp_path):
    record = Normaliser.for_run("run-1", tmp_path / "ws1", {str(tmp_path / "t1"): "<tmp>"})
    replay = Normaliser.for_run("run-2", tmp_path / "ws2", {str(tmp_path / "t2"): "<tmp>"})
    stored = record.replace_literals(f"{tmp_path}/t1/in.pdf {tmp_path}/ws1/o run-1 2026-09-22T21:50:01Z")
    assert stored == "<tmp>/in.pdf <workspace>/o <run.id> 2026-09-22T21:50:01Z"
    assert replay.restore(stored) == f"{tmp_path}/t2/in.pdf {tmp_path}/ws2/o run-2 2026-09-22T21:50:01Z"


def test_generate_key_includes_provider_tier_thinking_and_model_id():
    base = request_key(generate_request(_gen(), provider="anthropic", tier="cheap"), NO_LITERALS)
    variants = [
        generate_request(_gen(model_id="claude-sonnet-5"), provider="anthropic", tier="cheap"),
        generate_request(_gen(), provider="fake", tier="cheap"),
        generate_request(_gen(), provider="anthropic", tier="standard"),
        generate_request(_gen(thinking="high"), provider="anthropic", tier="cheap"),
        generate_request(_gen(system="Something else."), provider="anthropic", tier="cheap"),
        generate_request(_gen(output_schema=None), provider="anthropic", tier="cheap"),
        generate_request(_gen(tools=[]), provider="anthropic", tier="cheap"),
    ]
    keys = {request_key(v, NO_LITERALS) for v in variants}
    assert base not in keys
    assert len(keys) == len(variants)


def test_agent_key_ignores_the_run_workspace_path_and_streaming_callbacks(tmp_path):
    ws1, ws2 = tmp_path / "run-1", tmp_path / "run-2"
    first = agent_request(_agent(ws1), provider="claude-code", tier="cheap")
    second = agent_request(_agent(ws2, on_event=print), provider="claude-code", tier="cheap")
    assert first["workspace"] != second["workspace"]
    assert request_key(first, Normaliser.for_run("run-1", ws1)) == request_key(second, Normaliser.for_run("run-2", ws2))


def test_agent_key_includes_model_id_input_tools_mcp_and_builtins(tmp_path):
    norm = Normaliser.for_run("run_1", tmp_path)
    base = request_key(agent_request(_agent(tmp_path), provider="claude-code", tier="cheap"), norm)
    variants = [
        _agent(tmp_path, model_id="sonnet"),
        _agent(tmp_path, input={"invoice_text": "INVOICE INV-1043"}),
        _agent(tmp_path, tools=[]),
        _agent(tmp_path, mcp_servers=[]),
        _agent(tmp_path, builtin_tools=["Read"]),
        _agent(tmp_path, max_turns=5),
        _agent(tmp_path, prompt="raw"),
    ]
    keys = {request_key(agent_request(v, provider="claude-code", tier="cheap"), norm) for v in variants}
    assert base not in keys
    assert len(keys) == len(variants)


def test_continuation_keys_chain_on_the_previous_key_and_message():
    k = request_key(continue_request("a" * 64, "fix the total"), NO_LITERALS)
    assert k != request_key(continue_request("b" * 64, "fix the total"), NO_LITERALS)
    assert k != request_key(continue_request("a" * 64, "fix the date"), NO_LITERALS)
    assert k == request_key(continue_request("a" * 64, "fix the total"), NO_LITERALS)


def test_tool_key_depends_on_arguments_not_on_timestamps():
    a = request_key(tool_request("fetch", {"url": "https://x.test", "at": "2026-09-22T21:50:01Z"}), NO_LITERALS)
    b = request_key(tool_request("fetch", {"url": "https://x.test", "at": "2026-09-23T08:00:00Z"}), NO_LITERALS)
    c = request_key(tool_request("fetch", {"url": "https://y.test", "at": "2026-09-22T21:50:01Z"}), NO_LITERALS)
    assert a == b != c

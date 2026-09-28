"""CMP-D: ProviderLLM, MemoLLM, default_llm, the call table, and the offline doubles in `wynd.compiler.testing`."""

import shutil
import subprocess
import typing
from pathlib import Path

import pytest
import yaml

from wynd.compiler import llm as llm_mod
from wynd.compiler.calls import CALLS, CallKind, DecideResponse, WriteCodeResponse
from wynd.compiler.llm import REPAIR_TEXT, CompilerLLMError, MemoLLM, ProviderLLM, Tier, call_key, default_llm
from wynd.compiler.session import Memo
from wynd.compiler.testing import FakeJobContext, RecordingLLM, ScriptedLLM, ScriptMiss
from wynd.process.jobs import JobOutcome
from wynd.runtime.agentic.schema import DROP, strict_compatible, wrap_output_schema
from wynd.runtime.providers import register_for_tests
from wynd.runtime.providers.scripted import ScriptedAgentProvider, ScriptedModelProvider
from wynd.runtime.providers.types import AgentResponse, GenerateResponse, ProviderError
from wynd.runtime.storage.local import FileRegistry
from wynd.runtime.usage import Usage

SCRIPTS = Path(__file__).parent / "fixtures" / "scripts"
REPO = Path(__file__).resolve().parents[3]
TIERS = {"cheap": "m-cheap", "standard": "m-standard", "strong": "m-strong"}
DECIDE = {"examples": [{"index": 1, "needs": "pure", "why": "digits"}], "command": False, "program": "",
          "summary": "Pure."}


def agent_response(output, usage=None) -> AgentResponse:
    return AgentResponse(structured_output=output, usage=usage or Usage(input_tokens=10, output_tokens=5, calls=1),
                         model_id="m-strong", transcript=[], session={})


def model_response(output, usage=None) -> GenerateResponse:
    return GenerateResponse(message={"role": "assistant", "content": []}, text="", tool_calls=[],
                            structured_output=output, stop="end",
                            usage=usage or Usage(input_tokens=7, output_tokens=3, calls=1), model_id="m-standard")


def decide_call(llm, *, prompt="the prompt", node="parse"):
    return llm.call("decide", node=node, system="the system", prompt=prompt, response_model=DecideResponse,
                    tier=Tier.STRONG, thinking="medium")


@pytest.fixture
def no_sleep(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(llm_mod.time, "sleep", slept.append)
    return slept


# --- ProviderLLM -----------------------------------------------------------------------------------------------------

def test_agent_provider_request_is_raw_mode_with_the_plain_schema(tmp_path):
    provider = ScriptedAgentProvider([agent_response(DECIDE)])
    result = decide_call(ProviderLLM(provider, TIERS, tmp_path / "llm"))

    assert result.value == DecideResponse.model_validate(DECIDE)
    assert result.memo_hit is False
    assert result.usage.input_tokens == 10
    (req,) = provider.requests
    assert (req.instruction, req.prompt) == ("the system", "the prompt")
    assert (req.context, req.input, req.tools, req.mcp_servers, req.builtin_tools) == ({}, {}, [], [], [])
    assert req.output_schema == DecideResponse.model_json_schema()        # unwrapped: the provider wraps it
    assert (req.model_id, req.thinking) == ("m-strong", "medium")
    assert req.workspace == tmp_path / "llm" and req.workspace.is_dir()
    assert req.cancel is not None and not req.cancel.is_set()


def test_model_provider_gets_one_user_message(tmp_path):
    provider = ScriptedModelProvider([model_response(DECIDE)])
    result = ProviderLLM(provider, TIERS, tmp_path).call(
        "infer_schema", node="n", system="sys", prompt="user text", response_model=DecideResponse,
        tier=Tier.STANDARD, thinking="low")

    assert result.value.summary == "Pure."
    (req,) = provider.requests
    assert req.system == "sys"
    assert req.messages == [{"role": "user", "content": [{"type": "text", "text": "user text"}]}]
    assert (req.tools, req.model_id, req.thinking) == ([], "m-standard", "low")
    assert req.output_schema == DecideResponse.model_json_schema()


def test_invalid_answer_is_repaired_once_with_the_error_appended(tmp_path):
    bad = {**DECIDE, "examples": [{"index": "one", "needs": "magic", "why": "?"}]}
    provider = ScriptedAgentProvider([agent_response(bad), agent_response(DECIDE)])
    result = decide_call(ProviderLLM(provider, TIERS, tmp_path))

    assert result.value.summary == "Pure."
    first, second = provider.requests
    assert first.prompt == "the prompt"
    assert second.prompt.startswith("the prompt\n\n" + REPAIR_TEXT)
    assert "needs" in second.prompt and "magic" in second.prompt
    assert result.usage.input_tokens == 20 and result.usage.calls == 2   # both requests are paid for


def test_missing_output_counts_as_invalid_and_two_failures_raise(tmp_path):
    provider = ScriptedAgentProvider([agent_response(None), agent_response({"summary": 3})])
    with pytest.raises(CompilerLLMError, match="decide for 'parse'.*did not match the schema"):
        decide_call(ProviderLLM(provider, TIERS, tmp_path))
    assert len(provider.requests) == 2
    assert "no structured output" in provider.requests[1].prompt


def test_transport_errors_retry_twice_with_backoff(tmp_path, no_sleep):
    flaky = ProviderError("socket closed", kind="transport", retryable=True)
    provider = ScriptedAgentProvider([flaky, flaky, agent_response(DECIDE)])
    assert decide_call(ProviderLLM(provider, TIERS, tmp_path)).value.summary == "Pure."
    assert len(provider.requests) == 3
    assert no_sleep == [5.0, 20.0]


def test_transport_errors_give_up_after_the_retries(tmp_path, no_sleep):
    flaky = ProviderError("overloaded", kind="transport", retryable=True, status=529)
    provider = ScriptedAgentProvider([flaky, flaky, flaky])
    with pytest.raises(CompilerLLMError, match="transport: overloaded"):
        decide_call(ProviderLLM(provider, TIERS, tmp_path))
    assert len(provider.requests) == 3


def test_non_retryable_errors_fail_at_once(tmp_path, no_sleep):
    provider = ScriptedAgentProvider([ProviderError("Not logged in", kind="auth", retryable=False)])
    with pytest.raises(CompilerLLMError, match="auth: Not logged in") as info:
        decide_call(ProviderLLM(provider, TIERS, tmp_path))
    assert isinstance(info.value.__cause__, ProviderError)
    assert len(provider.requests) == 1 and no_sleep == []


def test_agent_requests_are_cancelled_at_the_timeout(tmp_path):
    class Hanging:
        name = "hanging"
        kind = "agent"

        def run(self, req):
            assert req.cancel.wait(5), "the timeout never set cancel"
            raise ProviderError("cancelled", kind="transport", retryable=False)

    with pytest.raises(CompilerLLMError, match="timed out after 0.05 s"):
        decide_call(ProviderLLM(Hanging(), TIERS, tmp_path, timeout_s=0.05))


def test_unknown_tier_is_an_error(tmp_path):
    provider = ScriptedAgentProvider([])
    with pytest.raises(CompilerLLMError, match="no model for tier strong"):
        decide_call(ProviderLLM(provider, {"cheap": "m"}, tmp_path))
    assert provider.requests == []


# --- MemoLLM ---------------------------------------------------------------------------------------------------------

def test_memo_miss_calls_through_records_and_reports_usage(tmp_path):
    provider = ScriptedAgentProvider([agent_response(DECIDE, Usage(input_tokens=11, cost_usd=0.5, calls=1))])
    memo, seen = Memo(), []
    llm = MemoLLM(ProviderLLM(provider, TIERS, tmp_path), memo,
                  lambda kind, node, usage: seen.append((kind, node, usage)))

    result = decide_call(llm)

    assert result.memo_hit is False and result.usage.input_tokens == 11
    key = call_key("decide", tier="strong", thinking="medium", system="the system", prompt="the prompt",
                   schema=DecideResponse.model_json_schema())
    assert memo.calls[key] == {"kind": "decide", "step": "parse", "response": DECIDE,
                               "usage": Usage(input_tokens=11, cost_usd=0.5, calls=1).model_dump(mode="json")}
    assert seen == [("decide", "parse", result.usage)]


def test_memo_hit_skips_the_provider_and_costs_nothing(tmp_path):
    provider = ScriptedAgentProvider([agent_response(DECIDE)])
    memo, seen = Memo(), []
    llm = MemoLLM(ProviderLLM(provider, TIERS, tmp_path), memo, lambda *args: seen.append(args))
    decide_call(llm)

    # the memo survives the session's JSON round trip
    again = MemoLLM(ProviderLLM(provider, TIERS, tmp_path), Memo.model_validate_json(memo.model_dump_json()),
                    lambda *args: seen.append(args))
    result = decide_call(again, node="another node")      # the node is not part of the key

    assert result.memo_hit is True
    assert result.value == DecideResponse.model_validate(DECIDE)
    assert result.usage == Usage()
    assert len(provider.requests) == 1 and len(seen) == 1


def test_memo_misses_when_any_part_of_the_request_changes(tmp_path):
    provider = ScriptedAgentProvider([agent_response(DECIDE)] * 2)
    llm = MemoLLM(ProviderLLM(provider, TIERS, tmp_path), Memo(), lambda *args: None)
    decide_call(llm)
    decide_call(llm, prompt="the prompt, with a new answer")
    assert len(provider.requests) == 2


def test_call_key_depends_on_every_field():
    base = {"tier": "strong", "thinking": "medium", "system": "s", "prompt": "p", "schema": {"type": "object"}}
    key = call_key("decide", **base)
    assert len(key) == 32 and int(key, 16) >= 0
    assert call_key("write_shell", **base) != key
    for field, other in [("tier", "standard"), ("thinking", "high"), ("system", "s2"), ("prompt", "p2"),
                         ("schema", {"type": "string"})]:
        assert call_key("decide", **{**base, field: other}) != key, field
    assert call_key("decide", **{**base, "tier": Tier.STRONG}) == key


# --- default_llm -----------------------------------------------------------------------------------------------------

def test_default_llm_uses_the_compiler_provider_and_registry_tiers(tmp_path, monkeypatch):
    provider = ScriptedAgentProvider([agent_response(DECIDE)])
    undo = register_for_tests("scripted-compiler", provider)
    try:
        registry = FileRegistry(tmp_path / "home")
        registry.put("providers", "scripted-compiler", {"tiers": {"strong": "big-model"}})
        monkeypatch.setenv("WYND_COMPILER_PROVIDER", "scripted-compiler")
        llm = default_llm(registry, tmp_path / "llm")
        decide_call(llm)
    finally:
        undo()
    assert isinstance(llm, ProviderLLM) and llm.provider is provider
    assert llm.tiers == {"cheap": "scripted", "standard": "scripted", "strong": "big-model"}
    assert provider.requests[0].model_id == "big-model"


def test_default_llm_defaults_to_claude_code(tmp_path, monkeypatch):
    monkeypatch.delenv("WYND_COMPILER_PROVIDER", raising=False)
    llm = default_llm(FileRegistry(tmp_path / "home"), tmp_path)
    assert llm.provider.name == "claude-code"
    assert llm.tiers == {"cheap": "haiku", "standard": "sonnet", "strong": "opus"}


# --- call table and response schemas ---------------------------------------------------------------------------------

def test_call_table_covers_every_kind_with_the_draft_tiers():
    assert set(CALLS) == set(typing.get_args(CallKind))
    assert {k: (c.tier, c.thinking) for k, c in CALLS.items()} == {
        "infer_schema": ("standard", "low"), "propose_examples": ("strong", "medium"),
        "revise_example": ("standard", "low"), "decide": ("strong", "medium"),
        "write_deterministic": ("strong", "medium"), "write_shell": ("strong", "medium"),
        "write_agentic": ("strong", "medium"), "revise_code": ("strong", "high"),
        "revise_agentic": ("strong", "medium")}


def _objects(node, path="$"):
    match node:
        case dict():
            if node.get("type") == "object" or "properties" in node:
                yield path, node
            for key, value in node.items():
                yield from _objects(value, f"{path}.{key}")
        case list():
            for i, value in enumerate(node):
                yield from _objects(value, f"{path}[{i}]")


def _keys(node):
    match node:
        case dict():
            for key, value in node.items():
                yield key
                if key != "properties":
                    yield from _keys(value)
                else:
                    for sub in value.values():
                        yield from _keys(sub)
        case list():
            for value in node:
                yield from _keys(value)


def _refs(node):
    match node:
        case dict():
            if "$ref" in node:
                yield node["$ref"]
            for value in node.values():
                yield from _refs(value)
        case list():
            for value in node:
                yield from _refs(value)


@pytest.mark.parametrize("kind", sorted(CALLS))
def test_every_response_schema_is_strict_after_the_provider_envelope(kind):
    model = CALLS[kind].response_model
    wrapped = wrap_output_schema(model.model_json_schema())
    assert strict_compatible(wrapped)
    for path, obj in _objects(wrapped):
        assert obj.get("additionalProperties") is False, path
        assert sorted(obj["required"]) == sorted(obj["properties"]), path
    assert not set(_keys(wrapped)) & DROP
    defs = wrapped.get("$defs", {})
    for name, sub in defs.items():            # no recursion: a definition never refers to itself, even indirectly
        seen, todo = set(), [f"#/$defs/{name}"]
        while todo:
            ref = todo.pop()
            target = defs[ref.removeprefix("#/$defs/")]
            for inner in _refs(target):
                assert inner != f"#/$defs/{name}", f"{name} is recursive"
                if inner not in seen:
                    seen.add(inner)
                    todo.append(inner)


# --- ScriptedLLM and RecordingLLM ------------------------------------------------------------------------------------

def test_scripted_llm_answers_in_order_per_kind_and_node():
    llm = ScriptedLLM(SCRIPTS / "unit.yaml")
    first = decide_call(llm, prompt="first parse")
    shout = decide_call(llm, node="shout")
    second = decide_call(llm, prompt="second parse")

    assert first.value.summary.startswith("One numeric")
    assert shout.value.program == "tr"
    assert second.value.summary == "Both are pure."
    assert [(c.kind, c.node, c.n) for c in llm.calls] == [("decide", "parse", 1), ("decide", "shout", 1),
                                                          ("decide", "parse", 2)]
    assert llm.prompts("decide", "parse") == ["first parse", "second parse"]
    assert llm.calls[0].tier == "strong" and llm.calls[0].system == "the system"
    assert first.usage == Usage(calls=1) and first.memo_hit is False


def test_scripted_module_source_file_is_resolved_next_to_the_script():
    llm = ScriptedLLM(SCRIPTS / "unit.yaml")
    result = llm.call("write_deterministic", node="parse", system="s", prompt="p", response_model=WriteCodeResponse,
                      tier=Tier.STRONG, thinking="medium")
    assert result.value.module_source == (SCRIPTS / "unit_module.py").read_text()
    assert result.usage == Usage(input_tokens=120, output_tokens=40, cost_usd=0.01, latency_ms=900, calls=1)


def test_script_miss_names_the_missing_call():
    llm = ScriptedLLM({"calls": [{"kind": "decide", "node": "parse", "response": DECIDE}]})
    decide_call(llm)
    with pytest.raises(ScriptMiss) as info:
        decide_call(llm, prompt="## Step\n\nparse_amount " + "x" * 500)
    miss = info.value
    assert (miss.kind, miss.node, miss.n) == ("decide", "parse", 2)
    assert miss.prompt_head.startswith("## Step") and len(miss.prompt_head) == 200
    assert "call 2 of (decide, parse)" in str(miss)
    with pytest.raises(ScriptMiss):
        decide_call(llm, node="classify")
    assert len(llm.calls) == 1                     # misses are not counted as answered calls


def test_scripted_response_must_match_the_model():
    llm = ScriptedLLM({"calls": [{"kind": "decide", "node": "parse", "response": {"summary": "no examples"}}]})
    with pytest.raises(ValueError, match="examples"):
        decide_call(llm)


def test_recording_llm_writes_a_script_that_replays_identically(tmp_path):
    out = tmp_path / "recorded" / "script.yaml"
    recorder = RecordingLLM(ScriptedLLM(SCRIPTS / "unit.yaml"), out)
    live = [decide_call(recorder).value, decide_call(recorder, node="shout").value,
            recorder.call("write_deterministic", node="parse", system="s", prompt="p",
                          response_model=WriteCodeResponse, tier=Tier.STRONG, thinking="medium").value]

    data = yaml.safe_load(out.read_text())
    assert [(c["kind"], c["node"]) for c in data["calls"]] == [("decide", "parse"), ("decide", "shout"),
                                                               ("write_deterministic", "parse")]
    replay = ScriptedLLM(out)
    again = [decide_call(replay).value, decide_call(replay, node="shout").value,
             replay.call("write_deterministic", node="parse", system="s", prompt="p",
                         response_model=WriteCodeResponse, tier=Tier.STRONG, thinking="medium").value]
    assert again == live


# --- FakeJobContext --------------------------------------------------------------------------------------------------

def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path) -> Path:
    """A git repo whose workspace is the `ws/` subdirectory (a copy of the sample, without its big files)."""
    root = tmp_path / "repo"
    shutil.copytree(REPO / "examples" / "invoices", root / "ws",
                    ignore=shutil.ignore_patterns("cassettes", "*.pdf", "__pycache__", ".wynd"))
    (root / ".gitignore").write_text(".wynd/\n__pycache__/\n")
    (root / "notes.txt").write_text("outside the workspace\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


PID = "process_supplier_invoice"


def test_fake_job_context_is_a_detached_worktree_with_a_job_record(repo):
    ws = repo / "ws"
    base = git(repo, "rev-parse", "HEAD")
    ctx = FakeJobContext(ws, PID, inputs={"accept_proposals": True})

    assert ctx.job.job_kind == "compile" and ctx.job.status == "queued"
    assert ctx.job.base_commit == ctx.job.ref == base and ctx.job.workspace_rel == "ws"
    assert ctx.inputs == {"process": PID, "accept_proposals": True, "target_branch": "main"}
    assert ctx.session is None
    assert ctx.worktree == ws / ".wynd" / "jobs" / ctx.job.id / "checkout-1"
    assert ctx.workspace == ctx.worktree / "ws"
    assert (ctx.workspace / "wynd.yaml").is_file()
    assert git(ctx.worktree, "rev-parse", "HEAD") == base
    assert subprocess.run(["git", "-C", str(ctx.worktree), "symbolic-ref", "-q", "HEAD"]).returncode != 0  # detached
    assert (ctx.workspace_root, ctx.state_dir) == (ws, ws / ".wynd")
    assert ctx.scratch.is_dir() and not ctx.scratch.is_relative_to(ctx.worktree)
    assert ctx.record().id == ctx.job.id

    ctx.log("hello")
    assert ctx.lines == ["hello"]
    assert (ctx.state_dir / "jobs" / ctx.job.id / "job.log").read_text().endswith(" hello\n")


def test_fake_commit_stages_the_closure_only_with_the_job_trailer(repo):
    ctx = FakeJobContext(repo / "ws", PID)
    proc_dir = ctx.workspace / "processes" / PID
    (proc_dir / "steps" / "read_pdf" / "read_pdf.py").write_text("# regenerated\n")
    (proc_dir / "proto" / "fix_fields.yaml").unlink()
    (ctx.worktree / "notes.txt").write_text("changed outside the closure\n")

    sha = ctx.commit("wynd compile: WIP", None)

    assert sha == git(ctx.worktree, "rev-parse", "HEAD")
    changed = git(ctx.worktree, "show", "--name-status", "--format=", sha).splitlines()
    assert sorted(changed) == sorted([f"M\tws/processes/{PID}/steps/read_pdf/read_pdf.py",
                                      f"D\tws/processes/{PID}/proto/fix_fields.yaml"])
    assert git(ctx.worktree, "log", "-1", "--format=%B").endswith(f"Wynd-Job: {ctx.job.id}")
    assert ctx.commit("nothing new", None) is None
    assert git(repo, "status", "--porcelain") == ""          # the user's tree is untouched


def test_fake_finish_and_requeue_follow_the_harness(repo):
    ctx = FakeJobContext(repo / "ws", PID, inputs={"answers": {"a.example1": "accept"}})
    ctx.save_session({"version": 1, "state": "running"})
    assert ctx.sessions == [{"version": 1, "state": "running"}]
    assert ctx.record().session == {"version": 1, "state": "running"}

    (ctx.workspace / "processes" / PID / "steps" / "read_pdf" / "read_pdf.py").write_text("# wip\n")
    wip = ctx.commit("wynd compile: WIP", None)
    record = ctx.finish(JobOutcome(status="awaiting_input", commit=wip, questions=[{"id": "b.clarify1"}],
                                   session={"version": 1, "state": "awaiting_input"}))

    branch = f"wynd/compile/{PID}/{ctx.job.id}"
    assert (record.status, record.result_branch, record.result_commit) == ("awaiting_input", branch, wip)
    assert record.questions == [{"id": "b.clarify1"}]
    assert git(repo, "rev-parse", branch) == wip
    assert not ctx.worktree.exists()

    again = ctx.requeue({"b.clarify1": "use GBP"})
    assert again.job.id == ctx.job.id and again.job.attempt == 2 and again.job.ref == wip
    assert again.worktree.name == "checkout-2"
    assert git(again.worktree, "rev-parse", "HEAD") == wip
    assert (again.workspace / "processes" / PID / "steps" / "read_pdf" / "read_pdf.py").read_text() == "# wip\n"
    assert again.session == {"version": 1, "state": "awaiting_input"}
    assert again.inputs["answers"] == {"a.example1": "accept", "b.clarify1": "use GBP"}
    assert again.job.base_commit == ctx.job.base_commit

    failed = again.finish(JobOutcome(status="failed", error="process example 2 failed"))
    assert failed.status == "failed" and failed.error == {"message": "process example 2 failed", "detail": None}
    assert again.worktree.exists()                         # kept on failure, like the harness

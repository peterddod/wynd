"""step.lock.yaml, edges.lock.yaml, process.lock.yaml and the run plan (PLAN §3.6, §3.7, §3.11; $DRAFTS/01 §6.5)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from wynd.spec import (
    DEFAULT_RETRIES,
    BaseChoice,
    EdgeLockEntry,
    EdgesLock,
    EnvFragment,
    FragmentRecord,
    LockedVenv,
    LockedWheel,
    McpSnapshot,
    McpToolSnapshot,
    PlanNode,
    PlanProcess,
    PlanStep,
    PlanVenv,
    ProcessDoc,
    ProcessLock,
    RetryOverride,
    RetryPolicy,
    RunPlan,
    ShellLock,
    StepLock,
    ToolSnapshot,
    branch_key,
    check_hash,
    dump_lock,
    effective_retries,
    hash_obj,
    interface_from_fields,
    load_edges_lock,
    load_process_lock,
    load_step_lock,
    parse_type,
)

FIXTURES = Path(__file__).parent / "fixtures" / "locks"
HASH = "sha256:" + "ab" * 32


def lock(**fields) -> StepLock:
    return StepLock.model_validate({"name": "step", "kind": "agentic", "entrypoint": "step:Step", **fields})


def lock_error(**fields) -> dict:
    with pytest.raises(ValidationError) as err:
        lock(**fields)
    [error] = err.value.errors()
    return error


def test_load_fixture_locks():
    read = load_step_lock(FIXTURES / "read_pdf.lock.yaml")
    assert (read.kind, read.entrypoint, read.locked_deps) == ("deterministic", "read_pdf:ReadPdf", ["pypdf==6.19.0"])
    assert read.interface.exits == ["done"]
    assert read.retries is None and read.tier is None
    extract = load_step_lock(FIXTURES / "extract.lock.yaml")
    assert extract.builtin_tools == ["Read", "Grep"]
    assert extract.tools[1].allow == ["pdftotext"]
    assert extract.context == ["process.goal", "previous.summary", "steps.read.outputs.text"]


@pytest.mark.parametrize("field", ["provider", "tier", "thinking", "builtin_tools", "max_turns", "tools", "mcp",
                                   "context"])
def test_agentic_only_fields(field):
    values = {"provider": "anthropic", "tier": "strong", "thinking": "high", "builtin_tools": ["Read"],
              "max_turns": 5, "tools": [{"name": "t", "source": "method"}],
              "mcp": [{"server": "s", "allow": ["a"], "tools": [{"name": "a", "input_schema": {}}], "hash": HASH}],
              "context": ["process.goal"]}
    error = lock_error(kind="deterministic", **{field: values[field]})
    assert error["type"] == "E-LOCK"
    assert error["msg"] == f"{field} is only allowed on agentic steps (kind is deterministic)"
    assert error["ctx"]["loc"] == (field,)
    lock(**{field: values[field]}, effects=["network"])  # fine on an agentic step


def test_shell_block_only_on_shell_steps():
    assert lock_error(kind="deterministic", shell={"exit_codes": {0: "done"}})["msg"] == (
        "shell is only allowed on shell steps (kind is deterministic)")
    shell = lock(kind="shell", shell={"exit_codes": {"0": "done", "1": "missing", "*": "error"}})
    assert shell.shell.exit_codes == {0: "done", 1: "missing", "*": "error"}
    assert ShellLock().exit_codes == {0: "done", "*": "error"}


def test_shell_exit_codes_must_map_to_interface_exits():
    iface = interface_from_fields({}, {"done": {}, "missing": {}})
    lock(kind="shell", interface=iface.model_dump(), shell={"exit_codes": {0: "done", 1: "missing", "*": "error"}})
    error = lock_error(kind="shell", interface=iface.model_dump(), shell={"exit_codes": {0: "done", 2: "gone"}})
    assert error["type"] == "E-LOCK" and "exit code 2 maps to 'gone'" in error["msg"]


@pytest.mark.parametrize("builtin", ["Bash", "NotebookEdit", "Write"])
def test_builtin_tools_outside_the_harness_allowlist(builtin):
    error = lock_error(builtin_tools=["Read", builtin])
    assert error["type"] == "E-LOCK"
    assert error["msg"] == (
        f"builtin tool '{builtin}' is not allowed: harnesses run on the host in local mode and may execute "
        "model-written code only inside the process container (SPEC §3.8)"
    )


def test_harness_builtins_are_allowed():
    assert lock(builtin_tools=["Read", "Glob", "Grep", "WebFetch", "WebSearch"]).builtin_tools[-1] == "WebSearch"


def test_shell_tool_allow_rule():
    shell = {"name": "shell", "source": "library", "effects": ["shell"]}
    assert "non-empty allow list" in lock_error(effects=["shell"], tools=[shell])["msg"]
    lock(effects=["shell"], tools=[{**shell, "allow": ["pdftotext"]}])
    other = {"name": "fetch", "source": "method", "allow": ["curl"]}
    assert lock_error(tools=[other])["msg"] == "allow is only for the shell library tool (tool fetch)"
    method_shell = {"name": "shell", "source": "method", "allow": ["ls"]}
    assert lock_error(tools=[method_shell])["type"] == "E-LOCK"


def test_effects_must_cover_tools_and_mcp():
    web = {"name": "web_search", "source": "library", "effects": ["network"], "idempotent": True}
    assert lock_error(tools=[web])["msg"] == "effects must declare network (used by tool web_search)"
    mcp = {"server": "github", "allow": ["a"], "tools": [{"name": "a", "input_schema": {}}], "hash": HASH}
    assert lock_error(mcp=[mcp])["msg"] == "effects must declare network (used by mcp server github)"
    lock(effects=["network"], tools=[web], mcp=[mcp])


def test_tool_env_must_be_declared_in_the_fragment():
    tool = {"name": "web_search", "source": "library", "effects": ["network"], "env": ["BRAVE_API_KEY"]}
    error = lock_error(effects=["network"], tools=[tool])
    assert error["msg"] == "env var BRAVE_API_KEY of tool web_search is not declared in fragment.vars"
    lock(effects=["network"], tools=[tool], fragment={"vars": [{"name": "BRAVE_API_KEY", "secret": True}]})


def test_mcp_snapshot_rules():
    with pytest.raises(ValidationError):
        McpSnapshot(server="s", allow=[], tools=[], hash=HASH)
    tools = [McpToolSnapshot(name="search_issues", input_schema={"type": "object"})]
    snapshot = McpSnapshot(server="github", allow=["search_issues", "create_issue"], tools=tools,
                           hash=hash_obj([t.model_dump(mode="json") for t in tools]))
    error = lock_error(effects=["network"], mcp=[snapshot.model_dump()])
    assert error["ctx"]["loc"] == ("mcp", 0, "tools")


def test_context_entries_must_parse():
    error = lock_error(context=["process.goal", "steps.read.text"])
    assert (error["type"], error["ctx"]["loc"]) == ("E-CONTEXT", ("context", 1))


def test_retries_and_overrides():
    assert DEFAULT_RETRIES["agentic"] == RetryPolicy(run=2, validation=2, tool=1)
    assert DEFAULT_RETRIES["deterministic"] == DEFAULT_RETRIES["shell"] == RetryPolicy()
    policy = RetryPolicy(run=2, validation=2, tool=1)
    assert effective_retries(policy, None) is policy
    assert effective_retries(policy, RetryOverride(validation=0)) == RetryPolicy(run=2, validation=0, tool=1)
    assert effective_retries(policy, RetryOverride(run=5, tool=0)) == RetryPolicy(run=5, validation=2, tool=0)
    with pytest.raises(ValidationError):
        RetryPolicy(run=-1)


def test_step_lock_yaml_round_trip(tmp_path):
    original = load_step_lock(FIXTURES / "extract.lock.yaml")
    text = dump_lock(original)
    assert "provider" not in text and "compiled" not in text and "interface" not in text
    assert text.startswith("wynd: 1\nname: extract_invoice_fields\nkind: agentic\n")
    path = tmp_path / "step.lock.yaml"
    path.write_text(text)
    assert load_step_lock(path) == original
    read = load_step_lock(FIXTURES / "read_pdf.lock.yaml")
    assert StepLock.model_validate_json(read.model_dump_json()) == read


def test_branch_key_and_check_hash():
    assert branch_key("validate.done", 1, None) == "validate.done[1]"
    assert branch_key("validate.done", 0, "save") == "validate.done[save]"
    assert branch_key("validate.done", 2, "") == "validate.done[2]"
    first = check_hash("The document is a final\n  invoice.", None)
    assert first == check_hash("The document is a final invoice.", ["previous.outputs"])
    assert first == hash_obj({"check": "The document is a final invoice.", "context": ["previous.outputs"]})
    assert first != check_hash("The document is a final invoice.", ["steps.read.outputs"])


def test_edges_lock(tmp_path):
    assert load_edges_lock(tmp_path / "missing.yaml") == EdgesLock()
    entry = EdgeLockEntry(check_hash=check_hash("final", ["steps.read.outputs"]))
    defaults = (entry.tier, entry.thinking, entry.retries, entry.timeout_s, entry.provider)
    assert defaults == ("cheap", "low", 2, 120, None)
    edges = EdgesLock(edges={"validate.done[save]": entry})
    path = tmp_path / "edges.lock.yaml"
    path.write_text(dump_lock(edges))
    assert load_edges_lock(path) == edges
    assert path.read_text().startswith("wynd: 1\nedges:\n  validate.done[save]:\n")


def run_plan(mode: str) -> RunPlan:
    definition = ProcessDoc.model_validate({
        "kind": "process", "name": "p", "entry": "read", "inputs": {"pdf_path": "path"},
        "outputs": {"text": "string"}, "steps": {"read": {"use": "./steps/read_pdf"}},
        "edges": [{"from": "read.done", "to": "$exit.done", "with": {"text": "steps.read.outputs.text",
                                                                        "due": "'2026-10-01'"}}],
        "examples": [{"inputs": {"pdf_path": "examples/a.pdf"}, "outputs": {"text": "x"}}],
    })
    step_lock = load_step_lock(FIXTURES / "read_pdf.lock.yaml")
    local = mode == "local"
    return RunPlan(
        mode=mode, root="p", commit=None if local else "c" * 40, provider="claude-code",
        venv_root="/ws/.wynd/venvs" if local else "/opt/wynd/venvs",
        venvs=[PlanVenv(id="v1", steps=["p#read_pdf"])],
        steps={"p#read_pdf": PlanStep(id="p#read_pdf", kind="deterministic", entrypoint="read_pdf:ReadPdf",
                                      venv="v1", package_dir="/ws/processes/p/steps/read_pdf" if local else None,
                                      lock=step_lock)},
        processes={"p": PlanProcess(id="p", dir="/ws/processes/p" if local else None, definition=definition,
                                    nodes={"read": PlanNode(step="p#read_pdf")})},
    )


@pytest.mark.parametrize("mode", ["local", "image"])
def test_run_plan_json_round_trip(mode):
    plan = run_plan(mode)
    again = RunPlan.model_validate_json(plan.model_dump_json(by_alias=True))
    assert again.model_dump(mode="json") == plan.model_dump(mode="json")
    assert again.processes["p"].definition.inputs == {"pdf_path": parse_type("path")}
    assert again.processes["p"].definition.edges[0].to[0].with_["due"] == "'2026-10-01'"


def test_process_lock_round_trips_as_json_and_yaml(tmp_path):
    process_lock = ProcessLock(
        process="p", commit="c" * 40, source_sha="d" * 40, process_hash=HASH, runtime_version="0.1.0",
        platform="linux/arm64",
        base=BaseChoice(requested="debian-slim-python", variant="slim", version="0.1.0", image="wynd-base:0.1.0-slim"),
        system_packages=["poppler-utils"],
        fragments=[FragmentRecord(source="step:p#read_pdf", deps=["pypdf>=6,<7"], system=[], requires=None)],
        wheels={"p#read_pdf": LockedWheel(dir="processes/p/steps/read_pdf", hash=HASH, wheel="dist/x.whl")},
        venvs=[LockedVenv(id="v1", inputs=["pypdf>=6,<7"], requirements=["pypdf==6.19.0"])],
        plan=run_plan("image"),
    )
    assert ProcessLock.model_validate_json(process_lock.model_dump_json(by_alias=True)).model_dump(
        mode="json") == process_lock.model_dump(mode="json")
    text = dump_lock(process_lock)
    assert "package_dir: null" in text and "dir: null" in text and "requires: null" in text
    assert "reason:" not in text and "python:" not in text
    path = tmp_path / "process.lock.yaml"
    path.write_text(text)
    assert load_process_lock(path).model_dump(mode="json") == process_lock.model_dump(mode="json")
    assert dump_lock(load_process_lock(path)) == text  # byte-identical re-dump


def test_tool_snapshot_defaults():
    assert ToolSnapshot(name="t", source="method").model_dump() == {
        "name": "t", "source": "method", "effects": [], "idempotent": False, "env": [], "allow": []}
    assert StepLock(name="s", kind="deterministic", entrypoint="s:S").fragment == EnvFragment()

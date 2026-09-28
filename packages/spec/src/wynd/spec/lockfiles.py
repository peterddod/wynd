"""`step.lock.yaml`, `edges.lock.yaml` and `process.lock.yaml` (PLAN §3.6, §3.7, §3.11; $DRAFTS/01 §6.5–§6.7)."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError

from wynd.spec.base import RESERVED_EXIT, EnvName, HashStr, Name, ProcessId, SpecModel
from wynd.spec.context import parse_context_entry
from wynd.spec.fragments import EnvFragment
from wynd.spec.hashing import hash_obj
from wynd.spec.interface import Interface
from wynd.spec.process_doc import RetryOverride
from wynd.spec.yamlio import dump_yaml, load_model

StepKind = Literal["deterministic", "agentic", "shell"]  # ProcessStep is never a package
TraceStepKind = Literal["deterministic", "agentic", "shell", "process"]  # "process" for ProcessStep nodes
Tier = Literal["cheap", "standard", "strong"]
Thinking = Literal["none", "low", "medium", "high"]
Effect = Literal["network", "filesystem", "shell"]
HARNESS_BUILTINS = ("Read", "Glob", "Grep", "WebFetch", "WebSearch")


class RetryPolicy(SpecModel):  # PLAN §3.9
    run: int = Field(0, ge=0)
    validation: int = Field(0, ge=0)
    tool: int = Field(0, ge=0)


DEFAULT_RETRIES: dict[StepKind, RetryPolicy] = {
    "deterministic": RetryPolicy(),
    "shell": RetryPolicy(),
    "agentic": RetryPolicy(run=2, validation=2, tool=1),
}


def effective_retries(policy: RetryPolicy, override: RetryOverride | None) -> RetryPolicy:
    """Field-wise override of `policy` by a branch's `limits.retries`."""
    if override is None:
        return policy
    return RetryPolicy(
        run=policy.run if override.run is None else override.run,
        validation=policy.validation if override.validation is None else override.validation,
        tool=policy.tool if override.tool is None else override.tool,
    )


class ToolSnapshot(SpecModel):
    name: str
    source: Literal["method", "library"]
    effects: list[Effect] = []
    idempotent: bool = False
    env: list[EnvName] = []
    allow: list[str] = []  # the `shell` builtin only: permitted executables (argv[0])


class McpToolSnapshot(SpecModel):
    name: str
    description: str = ""
    input_schema: dict[str, Any]
    output_schema: dict | None = None
    idempotent: bool = False


class McpSnapshot(SpecModel):
    server: str
    allow: list[str] = Field(min_length=1)
    tools: list[McpToolSnapshot]  # exactly the allowed tools, sorted by name
    hash: HashStr  # hash_obj([t.model_dump(mode="json") for t in tools])


class ShellLock(SpecModel):
    exit_codes: dict[int | Literal["*"], str] = {0: "done", "*": "error"}


class StepLock(SpecModel):
    wynd: Literal[1] = 1
    name: Name  # == package dir name
    kind: StepKind
    entrypoint: str = Field(pattern=r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")  # relative to the package
    proto_hash: HashStr | None = None  # None for proto-less hand-written steps
    provider: str | None = None  # agentic only; None = process default (hand-edited override, SPEC §3.9)
    tier: Tier | None = None  # agentic only; filled "cheap" when absent
    thinking: Thinking | None = None  # agentic only; filled "low" when absent
    builtin_tools: list[str] = []  # agentic only: AgentProvider harness built-ins; ⊆ HARNESS_BUILTINS
    max_turns: int | None = None  # agentic only; None -> 25
    retries: RetryPolicy | None = None  # None -> DEFAULT_RETRIES[kind]
    effects: list[Effect] = []
    tools: list[ToolSnapshot] = []  # agentic only
    mcp: list[McpSnapshot] = []  # agentic only
    context: list[str] = []  # agentic only; snapshot of the class attribute
    shell: ShellLock | None = None  # shell only
    fragment: EnvFragment = EnvFragment()  # declared deps (minimums), system, requires, env vars
    locked_deps: list[str] = []  # pinned closure "name==version", sorted, excluding wynd-spec/runtime
    interface: Interface | None = None  # snapshot of Input / per-exit Output (W129 when absent)
    compiled: dict[str, Any] | None = None  # compiler provenance (opaque to everyone but the compiler)

    @model_validator(mode="after")
    def _check(self) -> "StepLock":
        problem = _lock_problem(self)
        if problem is not None:
            code, message, loc = problem
            raise PydanticCustomError(code, "{message}", {"message": message, "loc": loc})
        return self


_AGENTIC_ONLY = ("provider", "tier", "thinking", "builtin_tools", "max_turns", "tools", "mcp", "context")


def _lock_problem(lock: StepLock) -> tuple[str, str, tuple] | None:
    """The first E-LOCK / E-CONTEXT violation of a step lock ($DRAFTS/01 §6.5 + PLAN §3.6), or None."""
    if lock.kind != "agentic":
        for field in _AGENTIC_ONLY:
            if getattr(lock, field) not in (None, []):
                return "E-LOCK", f"{field} is only allowed on agentic steps (kind is {lock.kind})", (field,)
    if lock.kind != "shell" and lock.shell is not None:
        return "E-LOCK", f"shell is only allowed on shell steps (kind is {lock.kind})", ("shell",)
    for i, name in enumerate(lock.builtin_tools):
        if name not in HARNESS_BUILTINS:
            return "E-LOCK", (
                f"builtin tool '{name}' is not allowed: harnesses run on the host in local mode and may execute "
                "model-written code only inside the process container (SPEC §3.8)"
            ), ("builtin_tools", i)
    if lock.max_turns is not None and lock.max_turns < 1:
        return "E-LOCK", "max_turns must be >= 1", ("max_turns",)
    for i, tool in enumerate(lock.tools):
        for effect in tool.effects:
            if effect not in lock.effects:
                return "E-LOCK", f"effects must declare {effect} (used by tool {tool.name})", ("effects",)
        is_shell = tool.name == "shell" and tool.source == "library"
        if is_shell and not tool.allow:
            return "E-LOCK", "the shell tool needs a non-empty allow list of executables", ("tools", i, "allow")
        if tool.allow and not is_shell:
            return "E-LOCK", f"allow is only for the shell library tool (tool {tool.name})", ("tools", i, "allow")
        declared = {var.name for var in lock.fragment.vars}
        for name in tool.env:
            if name not in declared:
                return "E-LOCK", f"env var {name} of tool {tool.name} is not declared in fragment.vars", (
                    "tools", i, "env")
    for i, snapshot in enumerate(lock.mcp):
        if "network" not in lock.effects:
            return "E-LOCK", f"effects must declare network (used by mcp server {snapshot.server})", ("effects",)
        names = {tool.name for tool in snapshot.tools}
        if names != set(snapshot.allow):
            return "E-LOCK", (
                f"mcp server {snapshot.server}: the snapshot tools ({', '.join(sorted(names)) or 'none'}) must be "
                f"exactly the allowed tools ({', '.join(sorted(snapshot.allow))})"
            ), ("mcp", i, "tools")
    if lock.shell is not None and lock.interface is not None:
        exits = {*lock.interface.exits, RESERVED_EXIT}
        for code, exit in lock.shell.exit_codes.items():
            if exit not in exits:
                return "E-LOCK", (
                    f"shell exit code {code} maps to '{exit}', which is not an interface exit "
                    f"({', '.join(sorted(exits))})"
                ), ("shell", "exit_codes", code)
    for i, entry in enumerate(lock.context):
        try:
            parse_context_entry(entry)
        except ValueError as err:
            return "E-CONTEXT", str(err), ("context", i)
    return None


class EdgeLockEntry(SpecModel):
    check_hash: HashStr  # check_hash(check, context)
    provider: str | None = None  # None -> RunPlan.provider (the ROOT process's effective default)
    tier: Tier = "cheap"
    thinking: Thinking = "low"
    retries: int = 2  # validation retries (continue the conversation)
    timeout_s: int = 120  # bounds the whole check incl. retries


class EdgesLock(SpecModel):
    wynd: Literal[1] = 1
    edges: dict[str, EdgeLockEntry] = {}  # key = branch_key


def branch_key(edge_from: str, index: int, name: str | None) -> str:
    """f"{edge_from}[{name if name else index}]" — the only branch reference format (PLAN §3.1)."""
    return f"{edge_from}[{name if name else index}]"


def check_hash(check: str, context: list[str] | None) -> str:
    """hash_obj({"check": " ".join(check.split()), "context": context or ["previous.outputs"]})."""
    return hash_obj({"check": " ".join(check.split()), "context": context or ["previous.outputs"]})


class BaseChoice(SpecModel):
    requested: Literal["debian-slim-python", "alpine-python"]
    variant: Literal["slim", "alpine"]
    version: str  # == wynd-runtime version
    image: str  # e.g. "wynd-base:0.1.0-slim"
    reason: str | None = None  # why alpine was not used (None when requested == used)


class FragmentRecord(SpecModel):
    source: str  # "step:<id>" | "provider:<name>"
    deps: list[str]
    system: list[str]
    requires: str | None


class LockedWheel(SpecModel):
    dir: str  # workspace-relative package dir
    hash: HashStr  # step_hash
    wheel: str  # dist/<file>


class LockedVenv(SpecModel):
    id: str
    inputs: list[str]
    requirements: list[str]  # resolved pins (incl. wynd-spec/runtime)


class ProcessLock(SpecModel):
    wynd: Literal[1] = 1
    process: ProcessId
    commit: str  # closure HEAD = "git commit hash of the compile tree"
    source_sha: str  # commit checked out by the job (same closure content)
    process_hash: HashStr
    runtime_version: str
    platform: str  # e.g. linux/arm64
    base: BaseChoice
    system_packages: list[str] = []
    fragments: list[FragmentRecord] = []
    wheels: dict[str, LockedWheel] = {}  # step id -> wheel
    venvs: list[LockedVenv] = []
    plan: "RunPlan"  # mode "image"; resolved when wynd.spec.plan is imported (bottom of this module)


def load_step_lock(path: Path) -> StepLock:
    return load_model(path, StepLock)


def load_edges_lock(path: Path) -> EdgesLock:
    """Missing file -> EdgesLock()."""
    if not Path(path).exists():
        return EdgesLock()
    return load_model(path, EdgesLock)


def load_process_lock(path: Path) -> ProcessLock:
    return load_model(path, ProcessLock)


def dump_lock(model: BaseModel) -> str:
    """Lockfile YAML text for any lock model: aliases, optional fields that are None left out (a required field
    whose value is None is kept, so the text always loads back into an equal model)."""
    data = model.model_dump(mode="python", by_alias=True)
    _prune_none(model, data)
    return dump_yaml(data)


def _prune_none(value: Any, data: Any) -> None:
    match value:
        case BaseModel():
            for name, info in type(value).model_fields.items():
                key = info.alias or name
                item = getattr(value, name)
                if item is None and not info.is_required():
                    data.pop(key, None)
                elif key in data:
                    _prune_none(item, data[key])
        case dict():
            for key, item in value.items():
                if key in data:
                    _prune_none(item, data[key])
        case list() | tuple():
            for item, dumped in zip(value, data):
                _prune_none(item, dumped)


# RunPlan embeds StepLock, so plan.py imports this module; importing it here (after every definition above) lets
# plan.py complete ProcessLock whichever of the two modules is imported first.
from wynd.spec import plan as _plan  # noqa: E402, F401

"""`step.lock.yaml`, `edges.lock.yaml` and `process.lock.yaml` (PLAN §3.6, §3.7, §3.11; $DRAFTS/01 §6.5–§6.7)."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from wynd.spec.base import EnvName, HashStr, Name, ProcessId, SpecModel
from wynd.spec.fragments import EnvFragment
from wynd.spec.interface import Interface
from wynd.spec.process_doc import RetryOverride

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
    raise NotImplementedError("PLAN §3.6")


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
    raise NotImplementedError("PLAN §3.7")


def check_hash(check: str, context: list[str] | None) -> str:
    """hash_obj({"check": " ".join(check.split()), "context": context or ["previous.outputs"]})."""
    raise NotImplementedError("PLAN §3.7")


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
    raise NotImplementedError("PLAN §3.6")


def load_edges_lock(path: Path) -> EdgesLock:
    """Missing file -> EdgesLock()."""
    raise NotImplementedError("PLAN §3.7")


def load_process_lock(path: Path) -> ProcessLock:
    raise NotImplementedError("PLAN §3.11")


def dump_lock(model: BaseModel) -> str:
    """Lockfile YAML text for any lock model."""
    raise NotImplementedError("PLAN §4.1")


# RunPlan embeds StepLock, so plan.py imports this module; importing it here (after every definition above) lets
# plan.py complete ProcessLock whichever of the two modules is imported first.
from wynd.spec import plan as _plan  # noqa: E402, F401

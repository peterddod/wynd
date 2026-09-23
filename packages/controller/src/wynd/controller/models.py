"""M1/M2 web DTOs and controller models (PLAN §3.21 amendments 10-13, §8.1; `$DRAFTS/07 §12.2`, `$DRAFTS/06 §10.4`).

The web contract is `$DRAFTS/07 §12.2` as amended by PLAN §3.21. `Usage`, `JobUsage`, `JobKind`, `JobStatus`,
`Integration` (= `IntegrationResult`), `ProcessError` and `EnvVar` are the single definitions owned by spec, runtime
and process; they are re-exported here, never redefined. M3/M4 DTOs live in `wynd.controller.api.models_web`.
Fields marked "controller addition" are not in the TS contract; they only extend it.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from wynd.process.git import IntegrationResult
from wynd.process.jobs import JobKind, JobStatus, JobUsage
from wynd.runtime.usage import Usage
from wynd.spec.fragments import EnvVar
from wynd.spec.lockfiles import TraceStepKind
from wynd.spec.records import ProcessError

__all__ = [
    "DTO", "Loc", "StatusFlag", "StepPhase", "InterfaceSource", "TriggerKind", "ReleaseState", "RunTrigger",
    "Usage", "JobUsage", "JobKind", "JobStatus", "Integration", "ProcessError", "EnvVar",
    "ExitSchema", "Interface", "InterfaceExample", "ProcessInterface",
    "WorkspaceInfo", "LlmStatus", "Meta",
    "Issue", "ValidationReportDTO", "EnvCheckDTO",
    "HeadInfo", "ReleaseRef", "ProcessStatus", "Match", "ProcessSummary",
    "StepLockInfo", "StepInfo", "StepCatalogEntry",
    "ExprCheckRequest", "ExprIssue", "ExprCheck",
    "ProviderInfoDTO",
    "PassCounts", "BuildTests", "BuildResult", "JobError", "Job",
    "LocalTarget", "ImageTarget", "ReleaseTarget", "RunTarget", "CreateRunRequest", "Run",
    "Build",
    "CommitInfo", "FileContent", "StepSource", "LogChunk", "RemoveResult", "InitResult", "ServedContainer",
    "EnvCheckRow", "EnvCheckReport", "TriggerFire",
]


class DTO(BaseModel):
    """Base of every controller DTO: closed, and serialised by alias (a field may be named after a BaseModel attribute)."""

    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)


Loc = list[str | int]
StatusFlag = Literal["design", "compiled", "built", "released"]
StepPhase = Literal["design", "compiled", "handwritten", "missing"]
InterfaceSource = Literal["declared", "inferred", "compiled", "process"]
TriggerKind = Literal["manual", "schedule", "webhook"]
ReleaseState = Literal["starting", "serving", "stopped", "error"]
RunTrigger = Literal["manual", "schedule", "webhook", "api"]

Integration = IntegrationResult  # PLAN §3.21 amendment 12


# --- interfaces (JSON Schemas; path-typed fields carry format "path", amendment 2) -----------------------------------

class ExitSchema(DTO):
    name: str
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")


class Interface(DTO):
    inputs: dict[str, Any] | None = None           # object schema
    exits: list[ExitSchema] = []                   # declared exits; the implicit `error` is excluded
    source: InterfaceSource | None = None          # lock->"compiled", proto->"declared", examples->"inferred"


class InterfaceExample(DTO):
    inputs: dict[str, Any] = {}
    outputs: Any = None
    exit: str


class ProcessInterface(DTO):
    commit: str | None = None
    inputs: dict[str, Any] | None = None
    outputs: dict[str, dict[str, Any] | None] = {}  # by exit
    examples: list[InterfaceExample] = []


# --- meta -------------------------------------------------------------------------------------------------------------

class WorkspaceInfo(DTO):
    root: str
    branch: str | None = None                      # None when HEAD is detached
    head: str | None = None
    process_roots: list[str]
    step_roots: dict[str, str]


class LlmStatus(DTO):
    provider: str
    ready: bool
    message: str                                   # check_provider(provider)[1] (PLAN §8)


class Meta(DTO):
    version: str
    workspace: WorkspaceInfo
    proto_types: list[str]
    bases: list[str]
    latency: list[str]                             # LATENCIES: ["fast", "normal"]
    edge_kinds: list[str]
    expr_functions: list[str]
    limit_fields: list[str]
    default_max_traversals: int
    default_provider: str
    llm: LlmStatus


# --- validation and env -----------------------------------------------------------------------------------------------

class Issue(DTO):
    """`Diagnostic.to_json()` subset (amendment 1)."""

    severity: Literal["error", "warning", "info"]
    code: str
    message: str
    file: str | None = None
    loc: Loc = []
    span: tuple[int, int] | None = None


class ValidationReportDTO(DTO):
    """Built from `wynd.process.ValidationReport` (amendment 13)."""

    ok: bool
    issues: list[Issue] = []


class EnvCheckDTO(DTO):
    """Built from spec `EnvCheck`: missing = E-ENV-MISSING/E-ENV-ONE-OF names, unbound = W-ENV-ONE-OF names."""

    ok: bool
    missing: list[str] = []
    unbound: list[str] = []
    issues: list[Issue] = []


# --- status and listing -----------------------------------------------------------------------------------------------

class HeadInfo(DTO):
    commit: str
    short: str
    at: datetime
    subject: str


class ReleaseRef(DTO):
    id: str
    commit: str
    short: str
    behind: int
    trigger: TriggerKind
    state: ReleaseState


class ProcessStatus(DTO):
    head: HeadInfo | None = None                   # None while the closure has no commit
    design: bool
    compiled: bool
    built: bool
    released: bool
    tests: Literal["passed", "failed", "unknown"]  # tests_status "missing" -> "unknown"
    design_steps: list[str] = []
    releases: list[ReleaseRef] = []
    dirty: list[str] = []                          # controller addition: dirty closure paths (never changes a flag)


class Match(DTO):
    field: Literal["id", "name", "goal", "instruction"]
    step: str | None = None
    snippet: str


class ProcessSummary(DTO):
    id: str
    name: str
    goal: str | None = None
    path: str                                      # workspace-relative process dir
    status: ProcessStatus | None = None            # None when the process fails to load (see error)
    matches: list[Match] = []
    error: str | None = None                       # controller addition: per-process load error ($DRAFTS/06 §5.5)


# --- steps ------------------------------------------------------------------------------------------------------------

class StepLockInfo(DTO):
    provider: str | None = None
    tier: str | None = None
    thinking: str | None = None
    effects: list[str] = []
    env: list[str] = []


class StepInfo(DTO):
    name: str
    use: str
    ref_kind: Literal["local", "root", "process"]
    resolved: bool
    proto_path: str | None = None                  # workspace-relative
    source_path: str | None = None                 # compiled package dir
    kind: TraceStepKind | None = None
    phase: StepPhase
    lock: StepLockInfo | None = None
    used_by: list[str] = []
    instruction: str | None = None
    interface: Interface


class StepCatalogEntry(DTO):
    use: str
    root: str
    path: str
    kind: TraceStepKind | None = None
    phase: StepPhase
    instruction: str | None = None
    used_by: list[str] = []


# --- expressions ------------------------------------------------------------------------------------------------------

class ExprCheckRequest(DTO):
    process_id: str
    process: Any                                   # the in-flight process document
    protos: dict[str, Any] = {}
    loc: Loc
    expr: str
    scope: bool = False


class ExprIssue(DTO):
    message: str
    start: int
    end: int


class ExprCheck(DTO):
    ok: bool
    errors: list[ExprIssue] = []
    warnings: list[ExprIssue] = []
    scope: list[str] | None = None


# --- providers --------------------------------------------------------------------------------------------------------

class ProviderInfoDTO(DTO):
    """Built from `provider_info` + `provider_tiers` + `check_provider` (amendment 13)."""

    name: str
    kind: Literal["model", "agent"]
    tiers: dict[str, str]
    ready: bool
    message: str


# --- jobs -------------------------------------------------------------------------------------------------------------

class PassCounts(DTO):
    passed: int
    failed: int
    total: int


class BuildTests(PassCounts):
    source: Literal["ran", "registry"]


class BuildResult(DTO):
    commit: str
    image: str
    build_dir: str                                 # JobRecord.artefacts.build_dir (amendment 12)
    tests: BuildTests
    image_digest: str | None = None                # controller addition (CLI prints it when pushed)
    pushed: bool = False                           # controller addition


class JobError(DTO):
    message: str
    detail: str | None = None


class Job(DTO):
    id: str
    kind: JobKind                                  # JobRecord.job_kind
    process_id: str                                # JobRecord.process
    ref: str
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    branch: str | None = None                      # JobRecord.result_branch
    result_commit: str | None = None
    session: dict[str, Any] | None = None          # CompileSession JSON (api/models_web.py), from compile_view.session_dto
    build: BuildResult | None = None
    integration: Integration | None = None
    error: JobError | None = None
    usage: JobUsage = Field(default_factory=JobUsage)
    chat_id: str | None = None
    report: dict[str, Any] | None = None           # controller addition: JobRecord.report (CLI output)
    artefacts: dict[str, Any] = {}                 # controller addition: JobRecord.artefacts (CLI output)


# --- runs -------------------------------------------------------------------------------------------------------------

class LocalTarget(DTO):
    kind: Literal["local"] = "local"


class ImageTarget(DTO):
    kind: Literal["image"] = "image"
    commit: str | None = None                      # None: the build at the process HEAD (CLI default)


class ReleaseTarget(DTO):
    kind: Literal["release"] = "release"
    release_id: str


RunTarget = Annotated[LocalTarget | ImageTarget | ReleaseTarget, Field(discriminator="kind")]


class CreateRunRequest(DTO):
    process_id: str
    target: Annotated[LocalTarget | ImageTarget, Field(discriminator="kind")] = Field(default_factory=LocalTarget)
    inputs: dict[str, Any] = {}


class Run(DTO):
    """The `RunRecord` projection (amendment 11)."""

    id: str
    process_id: str
    commit: str | None = None
    mode: Literal["local", "image"]
    target: RunTarget
    release_id: str | None = None
    trigger: RunTrigger
    status: Literal["queued", "running", "succeeded", "failed"]
    inputs: dict[str, Any] = {}
    exit: str | None = None
    outputs: dict[str, Any] | None = None
    error: ProcessError | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = None
    usage: Usage | None = None


# --- builds -----------------------------------------------------------------------------------------------------------

class Build(DTO):
    process_id: str
    commit: str
    short: str
    image: str
    built_at: datetime
    job_id: str | None = None
    at_head: bool
    behind: int
    env: list[EnvVar] = []


# --- controller models ($DRAFTS/06 §10.4) -----------------------------------------------------------------------------

class CommitInfo(DTO):
    sha: str
    short: str
    subject: str
    author: str
    at: datetime
    message: str | None = None


class FileContent(DTO):
    path: str
    revision: str                                  # "sha256:<hex of file bytes>"
    content: str
    size: int
    truncated: bool = False


class StepSource(DTO):
    step: str
    package_dir: str
    lock_yaml: str | None = None
    files: list[FileContent] = []
    cassettes: list[dict[str, Any]] = []


class LogChunk(DTO):
    text: str
    offset: int                                    # next byte offset
    done: bool


class RemoveResult(DTO):
    removed: bool
    referenced_by: list[str] = []


class InitResult(DTO):
    root: str
    created: list[str]
    commit: str | None = None


class ServedContainer(DTO):
    """Also the `.wynd/serve/<name>.json` record ($DRAFTS/06 §10.5)."""

    name: str
    container_id: str
    image: str
    url: str
    process: str
    commit: str
    started_at: datetime
    stopped_at: datetime | None = None             # uptime accounting (SPEC §15)
    release_id: str | None = None
    env_hash: str | None = None                    # hash of image + env items, never the values
    state: ReleaseState = "serving"


class EnvCheckRow(DTO):
    name: str
    required: bool
    secret: bool
    description: str
    used_by: list[str]
    set: bool
    source: str                                    # "env" | "file" | "dotenv" | "registry" | "missing"


class EnvCheckReport(DTO):
    process: str
    manifest: Literal["build", "assembled"]
    commit: str | None = None
    ok: bool
    vars: list[EnvCheckRow]


class TriggerFire(DTO):
    id: str
    release_id: str
    source: TriggerKind
    at: datetime
    run_id: str | None = None
    ok: bool
    error: str | None = None
    started_at: datetime | None = None             # SPEC §15 accounting
    finished_at: datetime | None = None

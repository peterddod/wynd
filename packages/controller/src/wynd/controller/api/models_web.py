"""M3/M4 web DTOs (PLAN §3.21 amendment 15; `$DRAFTS/07 §12.2` as amended by PLAN §3.21).

Compile session (M3), design, chats, releases and the remaining API bodies (M4). M1/M2 DTOs live in
`wynd.controller.models`. Fields marked "controller addition" are not in the TS contract; they only extend it.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from wynd.controller.models import (
    DTO,
    Interface,
    Job,
    JobKind,
    JobStatus,
    PassCounts,
    ProcessInterface,
    ReleaseState,
    StepInfo,
    StepPhase,
    TraceStepKind,
    Usage,
    ValidationReportDTO,
)

__all__ = [
    "CompileSplit", "CompileDecision", "CompileStep", "ProposedExample", "ClarificationAnswer", "ProposalAnswer",
    "ClarificationQuestion", "ExampleProposalQuestion", "Question", "CompileEvent", "CompileSession",
    "TextAnswer", "DecisionAnswer", "AnswerRequest",
    "ParseError", "FileDoc", "ProcessFileDoc", "DesignConventions", "AvailableLocalStep", "LockOwner", "DesignDoc",
    "CommitReason", "SaveWrite", "SaveCommit", "SaveRequest", "SavedFile", "SavedCommit", "SaveResult",
    "ChatJob", "ChatSummary", "UserItem", "AssistantItem", "ToolItem", "CommitItem", "JobItem", "NoticeItem",
    "ChatItem", "ChatSnapshot", "SendMessageRequest", "SendMessageResult", "CreateChatRequest", "RenameChatRequest",
    "JobWithChat",
    "ManualTrigger", "ScheduleTrigger", "WebhookTrigger", "Trigger", "ValueBinding", "FromEnvBinding", "EnvBinding",
    "Release", "CreateReleaseRequest", "ReleasePatch", "TriggerRequest",
    "TraceEvent", "CreateProcessRequest", "TestLiveRequest", "UploadResult", "ProviderAddRequest", "OAuthStartRequest",
    "OAuthStart",
]


# --- compile session (M3; the compiler SessionData projection, amendment 9) -------------------------------------------

class CompileSplit(DTO):
    deterministic: str
    agentic: str


class CompileDecision(DTO):
    kind: TraceStepKind
    tier: str | None = None
    thinking: str | None = None
    reason: str
    split: CompileSplit | None = None


class CompileStep(DTO):
    step: str
    use: str
    phase: Literal["pending", "generating", "testing", "revising", "done", "skipped", "failed"]
    decision: CompileDecision | None = None
    tests: PassCounts | None = None
    attempts: int = 0
    skipped_reason: str | None = None


class ProposedExample(DTO):
    inputs: dict[str, Any] = {}
    outputs: Any = None
    exit: str


class ClarificationAnswer(DTO):
    text: str


class ProposalAnswer(DTO):
    decision: Literal["confirm", "correct", "reject"]
    example: Any = None
    text: str | None = None


class ClarificationQuestion(DTO):
    id: str
    kind: Literal["clarification"] = "clarification"
    step: str | None = None
    text: str
    status: Literal["pending", "answered"]
    answer: ClarificationAnswer | None = None
    asked_at: datetime
    detail: str | None = None                      # controller addition: SessionData question detail
    default: str | None = None                     # controller addition: SessionData question default


class ExampleProposalQuestion(DTO):
    id: str
    kind: Literal["example_proposal"] = "example_proposal"
    step: str
    text: str
    proposed: ProposedExample
    status: Literal["pending", "answered"]
    answer: ProposalAnswer | None = None
    asked_at: datetime
    detail: str | None = None                      # controller addition
    default: str | None = None                     # controller addition


Question = Annotated[ClarificationQuestion | ExampleProposalQuestion, Field(discriminator="kind")]


class CompileEvent(DTO):
    at: datetime
    type: str
    step: str | None = None
    text: str


class CompileSession(DTO):
    state: Literal["compiling", "awaiting_input", "done", "failed"]
    steps: list[CompileStep] = []
    questions: list[Question] = []
    inferred_schemas: dict[str, Interface] = {}
    events: list[CompileEvent] = []


class TextAnswer(DTO):
    question_id: str
    text: str


class DecisionAnswer(DTO):
    question_id: str
    decision: Literal["confirm", "correct", "reject"]
    example: Any = None
    text: str | None = None


# Answer text: confirm -> "accept", reject -> "reject", correct+example -> json.dumps(example), text -> text.
AnswerRequest = TextAnswer | DecisionAnswer


# --- design (M4) ------------------------------------------------------------------------------------------------------

class ParseError(DTO):
    message: str
    line: int | None = None
    column: int | None = None


class FileDoc(DTO):
    path: str
    revision: str | None = None                    # "sha256:<hex>"; None: the file does not exist
    doc: Any = None
    yaml: str


class ProcessFileDoc(FileDoc):
    parse_error: ParseError | None = None          # doc is None when set


class DesignConventions(DTO):
    local_use: str                                 # "{name}" placeholder
    local_proto_path: str


class AvailableLocalStep(DTO):
    use: str
    proto_path: str | None = None
    source_path: str | None = None
    phase: StepPhase


class LockOwner(DTO):
    chat_id: str
    turn_id: str


class DesignDoc(DTO):
    process_id: str
    head: str
    process_file: ProcessFileDoc
    protos: dict[str, FileDoc] = {}
    steps: dict[str, StepInfo] = {}
    interface: ProcessInterface
    available_local: list[AvailableLocalStep] = []
    conventions: DesignConventions
    validation: ValidationReportDTO
    locked_by: LockOwner | None = None


CommitReason = Literal["blur", "hidden", "process_switch", "before_job", "before_chat", "before_integrate", "unload"]


class SaveWrite(DTO):
    path: str
    base_revision: str | None = None               # None: the file must not exist
    doc: Any = None
    delete: bool = False


class SaveCommit(DTO):
    reason: CommitReason
    summary: str = ""


class SaveRequest(DTO):
    writes: list[SaveWrite] = []
    commit: SaveCommit | None = None


class SavedFile(DTO):
    path: str
    revision: str | None = None
    yaml: str | None = None


class SavedCommit(DTO):
    sha: str
    message: str


class SaveResult(DTO):
    files: list[SavedFile] = []
    validation: ValidationReportDTO
    steps: dict[str, StepInfo] = {}
    interface: ProcessInterface
    commit: SavedCommit | None = None
    head: str


# --- chats (M4) -------------------------------------------------------------------------------------------------------

class ChatJob(DTO):
    id: str
    kind: JobKind
    process_id: str
    status: JobStatus
    pending_questions: int = 0


class ChatSummary(DTO):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    running_turn: str | None = None
    job: ChatJob | None = None
    usage_totals: Usage | None = None              # controller addition ($DRAFTS/06 §7.7)


class UserItem(DTO):
    id: str
    seq: int
    type: Literal["user"] = "user"
    created_at: datetime
    text: str
    acting_on: str | None = None


class AssistantItem(DTO):
    id: str
    seq: int
    type: Literal["assistant"] = "assistant"
    created_at: datetime
    turn_id: str
    text: str = ""
    status: Literal["streaming", "done", "error", "cancelled"]
    error: str | None = None
    usage: Usage | None = None                     # controller addition ($DRAFTS/06 §7.7)


class ToolItem(DTO):
    id: str
    seq: int
    type: Literal["tool"] = "tool"
    created_at: datetime
    turn_id: str
    tool: str
    args: Any = None
    write: bool = False
    acting_on: str | None = None
    status: Literal["running", "ok", "error"]
    summary: str | None = None
    duration_ms: float | None = None


class CommitItem(DTO):
    id: str
    seq: int
    type: Literal["commit"] = "commit"
    created_at: datetime
    turn_id: str
    process_id: str
    sha: str
    message: str


class JobItem(DTO):
    id: str
    seq: int
    type: Literal["job"] = "job"
    created_at: datetime
    job_id: str
    kind: JobKind
    process_id: str


class NoticeItem(DTO):
    id: str
    seq: int
    type: Literal["notice"] = "notice"
    created_at: datetime
    level: Literal["info", "warning", "error"]
    text: str


ChatItem = Annotated[
    UserItem | AssistantItem | ToolItem | CommitItem | JobItem | NoticeItem, Field(discriminator="type")
]


class ChatSnapshot(DTO):
    chat: ChatSummary
    items: list[ChatItem] = []
    cursor: int = 0                                # last chat event id this snapshot reflects


class SendMessageRequest(DTO):
    text: str
    acting_on: str | None = None
    client_id: str


class SendMessageResult(DTO):
    turn_id: str
    item: ChatItem


class CreateChatRequest(DTO):
    title: str | None = None


class RenameChatRequest(DTO):
    title: str


class JobWithChat(DTO):
    """Response of the compile/build/test-live buttons: 201 `{job, chat}`."""

    job: Job
    chat: ChatSummary


# --- releases (M4) ----------------------------------------------------------------------------------------------------

class ManualTrigger(DTO):
    kind: Literal["manual"] = "manual"


class ScheduleTrigger(DTO):
    kind: Literal["schedule"] = "schedule"
    cron: str
    timezone: str | None = None                    # None: UTC
    inputs: dict[str, Any] = {}


class WebhookTrigger(DTO):
    kind: Literal["webhook"] = "webhook"
    secret_env: str | None = None


Trigger = Annotated[ManualTrigger | ScheduleTrigger | WebhookTrigger, Field(discriminator="kind")]


class ValueBinding(DTO):
    value: str


class FromEnvBinding(DTO):
    from_env: str


EnvBinding = ValueBinding | FromEnvBinding


class Release(DTO):
    id: str
    process_id: str
    commit: str
    short: str
    image: str
    behind: int = 0
    trigger: Trigger
    env: dict[str, EnvBinding] = {}
    enabled: bool
    state: ReleaseState
    state_detail: str | None = None
    created_at: datetime
    next_fire_at: datetime | None = None
    webhook_url: str | None = None


class CreateReleaseRequest(DTO):
    process_id: str
    commit: str
    trigger: Trigger
    env: dict[str, EnvBinding] = {}
    enabled: bool = True


class ReleasePatch(DTO):
    trigger: Trigger | None = None
    env: dict[str, EnvBinding] | None = None
    enabled: bool | None = None


class TriggerRequest(DTO):
    """`POST /api/releases/{id}/trigger` body (amendment 5)."""

    inputs: dict[str, Any] | None = None
    source: Literal["manual", "schedule"] = "manual"


# --- other API bodies -------------------------------------------------------------------------------------------------

class TraceEvent(BaseModel):
    """A PLAN §3.13 trace event: the envelope plus the type's own fields, passed through unchanged (amendment 4)."""

    model_config = ConfigDict(extra="allow")

    v: int
    seq: int
    ts: str
    run_id: str
    type: str


class CreateProcessRequest(DTO):
    id: str
    goal: str | None = None
    root: str | None = None


class TestLiveRequest(DTO):
    """`POST /api/processes/{pid}/test-live` body (`$DRAFTS/06 §8.3` (+) route)."""

    __test__ = False                               # not a pytest class

    steps: list[str] | None = None


class UploadResult(DTO):
    path: str


class ProviderAddRequest(DTO):
    """`POST /api/providers` body; no `env` field (amendment 8)."""

    name: str
    tiers: dict[Literal["cheap", "standard", "strong"], str] = {}


class OAuthStartRequest(DTO):
    return_to: str | None = None


class OAuthStart(DTO):
    authorize_url: str

"""The compile session (SPEC §9, §6.6; `$DRAFTS/05 §6`, PLAN §7 items 4 and 10).

A session is re-executed from the top on every job attempt against the tree, the cumulative answers and the memo; it
never serialises a program counter. Its JSON (`to_json`) is `JobRecord.session`; pending questions are also copied to
`JobRecord.questions`. Session id = the job id; answering requeues the same job (PLAN §3.18).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from wynd.compiler.pipeline import CompileEnv


class SessionState(StrEnum):
    NEW = "new"
    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    READY = "ready"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Answer(BaseModel):
    """The interpreted answer ($DRAFTS/05 §6.4 table)."""
    text: str                                                         # raw answer text as received
    decision: Literal["confirm", "correct", "reject"] | None = None   # proposals only
    example: dict | None = None                                       # the confirmed or corrected example


class Question(BaseModel):
    id: str                          # "<node>.example<n>", "<node>.clarify<k>"; "<child-pid>:<node>.…" in child processes
    kind: Literal["example_proposal", "clarification"]
    process: str
    step: str                        # node name in the process
    package: str                     # step package name, e.g. extract_invoice_fields
    text: str                        # one plain-language question
    detail: str = ""                 # markdown shown under it (example YAML, failing tests, diagnosis)
    proposed: dict | None = None     # example_proposal: {"inputs": …, "exit": …, "outputs": …}
    expects: Literal["decision", "text", "examples"] = "text"
    default: str | None = None       # "accept" for proposals; None for clarifications
    fingerprint: str                 # "sha256:" + hash(kind, text, canonical proposed)
    status: Literal["pending", "answered", "stale"] = "pending"
    answer: Answer | None = None
    answered_by: Literal["user", "accept_proposals"] | None = None
    asked_at: str
    asked_in_job: str


class SessionEvent(BaseModel):
    seq: int
    at: str                          # ISO-8601 UTC
    type: Literal["decision", "question", "answer", "note", "test", "split", "warning", "error", "commit"]
    process: str
    step: str | None                 # node name
    text: str                        # plain-language line
    data: dict = {}


class TestCounts(BaseModel):
    passed: int
    failed: int
    total: int


class StepDecision(BaseModel):
    kind: Literal["deterministic", "agentic", "shell", "process"]
    rule: Literal[1, 2, 3, 4] | None
    tier: str | None
    thinking: str | None
    reason: str
    split: dict | None = None        # {"deterministic": "<node>", "agentic": "<node>_agentic", "handled": [...], "deferred": [...]}


class CompileStep(BaseModel):
    """One row of the web's decisions table."""
    process: str
    step: str                        # node name
    use: str
    package: str | None
    phase: Literal["pending", "generating", "testing", "revising", "awaiting_input", "done", "skipped", "failed"]
    decision: StepDecision | None = None
    tests: TestCounts | None = None
    attempts: int = 0
    skipped_reason: str | None = None


class CompileOptions(BaseModel):
    accept_proposals: bool = False
    max_revisions: int = 3           # N in "revise up to N times"
    max_proposals: int = 3


class Memo(BaseModel):
    """Compiler-private memo ($DRAFTS/05 §6.5–§6.6): LLM calls and failing attempts' test summaries by key."""
    calls: dict[str, dict[str, Any]] = {}
    tests: dict[str, dict[str, Any]] = {}


class SessionData:
    """The serialised session ($DRAFTS/05 §6.3, §6.5): version, id (the job id), process, base_commit, state,
    options, jobs, steps, questions, inferred_schemas, events, pending_answers, memo, usage, report.
    Stub: fields land with CMP-A."""


class CompileSession:
    """SPEC §9's session API: `state`, `pending_questions`, `answer(question_id, text)`, `next(env)`."""

    data: SessionData

    def __init__(self, data: SessionData):
        self.data = data

    @classmethod
    def new(cls, *, session_id: str, process: str, base_commit: str, options: CompileOptions) -> CompileSession:
        raise NotImplementedError("PLAN §7")

    @classmethod
    def from_json(cls, data: dict) -> CompileSession:
        raise NotImplementedError("PLAN §7")

    def to_json(self) -> dict:
        raise NotImplementedError("PLAN §7")

    @property
    def state(self) -> SessionState:
        raise NotImplementedError("PLAN §7")

    @property
    def pending_questions(self) -> list[Question]:
        raise NotImplementedError("PLAN §7")

    def answer(self, question_id: str, text: str) -> None:
        """Legal in new (pre-answer), awaiting_input and ready; the state becomes ready when nothing is pending."""
        raise NotImplementedError("PLAN §7")

    def apply_answers(self, answers: Mapping[str, str], *, accept_proposals: bool) -> None:
        raise NotImplementedError("PLAN §7")

    def next(self, env: CompileEnv) -> SessionState:
        """Run the closure pipeline once (legal from new, ready and failed)."""
        raise NotImplementedError("PLAN §7")

    def cancel(self) -> None:
        raise NotImplementedError("PLAN §7")

    def ask(self, q: Question) -> Answer | None:
        """Pipeline-only; idempotent per question id and fingerprint ($DRAFTS/05 §6.4)."""
        raise NotImplementedError("PLAN §7")

    def emit(self, type: str, text: str, *, process: str, step: str | None, data: dict | None = None) -> None:
        """Pipeline-only: append a SessionEvent."""
        raise NotImplementedError("PLAN §7")

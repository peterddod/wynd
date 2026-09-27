"""The compile session (SPEC §9, §6.6; `$DRAFTS/05 §6`, PLAN §7 items 4 and 10).

A session is re-executed from the top on every job attempt against the tree, the cumulative answers and the memo; it
never serialises a program counter. Its JSON (`to_json`) is `JobRecord.session`; pending questions are also copied to
`JobRecord.questions`. Session id = the job id; answering requeues the same job (PLAN §3.18).

State machine ($DRAFTS/05 §6.2): `next` is legal from new, ready and failed; `answer` from new (pre-answers),
awaiting_input and ready (the state becomes ready when no question is pending); `cancel` from awaiting_input, ready
and failed. Illegal calls raise `SessionStateError`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal

import yaml
from pydantic import BaseModel

from wynd.compiler.report import CompileReport
from wynd.runtime.usage import Usage
from wynd.spec.hashing import hash_obj

if TYPE_CHECKING:
    from wynd.compiler.pipeline import CompileEnv

MEMO_LIMIT_BYTES = 2_000_000
ACCEPT_TEXTS = frozenset({"", "accept", "confirm", "yes", "y"})
REJECT_TEXTS = frozenset({"reject", "no", "skip", "not a real case"})


class SessionStateError(ValueError):
    """A session method called in a state where it is not legal."""


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
    __test__ = False                 # not a pytest test class
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

    @classmethod
    def from_inputs(cls, inputs: Mapping[str, Any]) -> CompileOptions:
        """Job inputs `accept_proposals`, `max_revisions`, `max_proposals` (each optional)."""
        return cls.model_validate({k: inputs[k] for k in cls.model_fields if inputs.get(k) is not None})


class Memo(BaseModel):
    """Compiler-private memo ($DRAFTS/05 §6.5–§6.6): LLM calls and failing attempts' test summaries by key.
    Each entry is a JSON object carrying at least `step` (the node it belongs to); insertion order is age."""
    calls: dict[str, dict[str, Any]] = {}
    tests: dict[str, dict[str, Any]] = {}


class SessionData(BaseModel):
    """The serialised session ($DRAFTS/05 §6.3, §6.5)."""
    version: Literal[1] = 1
    id: str                          # the job id; it names the branch
    process: str
    base_commit: str
    state: SessionState = SessionState.NEW
    options: CompileOptions = CompileOptions()
    jobs: list[str] = []
    steps: list[CompileStep] = []
    questions: list[Question] = []
    inferred_schemas: dict[str, dict] = {}   # "<node>" -> {"interface": Interface JSON, "plain": [lines]}
    events: list[SessionEvent] = []
    pending_answers: dict[str, str] = {}     # answers for ids not asked yet (pre-answers)
    memo: Memo = Memo()
    usage: Usage = Usage()                   # the compiler's own calls, totals over every attempt
    report: CompileReport = CompileReport()


def question_fingerprint(kind: str, text: str, proposed: Mapping[str, Any] | None) -> str:
    return hash_obj({"kind": kind, "text": text, "proposed": proposed})


def question_id(*, root: str, process: str, node: str, suffix: str) -> str:
    """`<node>.<suffix>` (e.g. `extract.example1`), prefixed `<process>:` for a child process of `root`."""
    local = f"{node}.{suffix}"
    return local if process == root else f"{process}:{local}"


def interpret(q: Question, text: str) -> Answer:
    """The answer-interpretation table of `$DRAFTS/05 §6.4`. Free text on a proposal is a correction whose example
    the pipeline derives (`revise_example`); clarifications keep the text as given."""
    if q.kind == "clarification":
        return Answer(text=text)
    word = text.strip().lower()
    if word in ACCEPT_TEXTS:
        return Answer(text=text, decision="confirm", example=q.proposed)
    if word in REJECT_TEXTS:
        return Answer(text=text, decision="reject")
    example = _example_mapping(text)
    if example is not None:
        return Answer(text=text, decision="correct", example=example)
    return Answer(text=text, decision="correct")


class CompileSession:
    """SPEC §9's session API: `state`, `pending_questions`, `answer(question_id, text)`, `next(env)`."""

    data: SessionData

    def __init__(self, data: SessionData):
        self.data = data

    @classmethod
    def new(cls, *, session_id: str, process: str, base_commit: str, options: CompileOptions) -> CompileSession:
        report = CompileReport(process=process, session=session_id, base_commit=base_commit)
        return cls(SessionData(id=session_id, process=process, base_commit=base_commit, options=options,
                               report=report))

    @classmethod
    def from_json(cls, data: dict) -> CompileSession:
        return cls(SessionData.model_validate(data))

    def to_json(self) -> dict:
        return self.data.model_dump(mode="json", by_alias=True)

    @property
    def state(self) -> SessionState:
        return self.data.state

    @property
    def pending_questions(self) -> list[Question]:
        return [q for q in self.data.questions if q.status == "pending"]

    def answer(self, question_id: str, text: str) -> None:
        """Legal in new (pre-answer), awaiting_input, ready and failed (a resumed job re-applies its cumulative
        answers); the state becomes ready when nothing is pending. An id not asked yet is kept as a pre-answer;
        re-answering with the same text changes nothing, so a failed session stays failed."""
        self._require("answer", SessionState.NEW, SessionState.AWAITING_INPUT, SessionState.READY,
                      SessionState.FAILED)
        q = self._current(question_id)
        if q is None:
            self.data.pending_answers[question_id] = text
        elif not (q.status == "answered" and q.answered_by == "user" and q.answer.text == text):
            self._record(q, interpret(q, text), "user")
        else:
            return
        if self.data.state != SessionState.NEW:
            self.data.state = SessionState.AWAITING_INPUT if self.pending_questions else SessionState.READY

    def apply_answers(self, answers: Mapping[str, str], *, accept_proposals: bool) -> None:
        """Every answer (cumulative across attempts), then, with `accept_proposals`, `accept` for every pending
        example proposal; the option is kept for questions asked later."""
        for question_id, text in answers.items():
            self.answer(question_id, text)
        if not accept_proposals:
            return
        self.data.options.accept_proposals = True
        proposals = [q for q in self.pending_questions if q.kind == "example_proposal"]
        if not proposals:
            return
        self._require("accept proposals", SessionState.AWAITING_INPUT, SessionState.READY, SessionState.FAILED)
        for q in proposals:
            self._record(q, interpret(q, "accept"), "accept_proposals")
        self.data.state = SessionState.AWAITING_INPUT if self.pending_questions else SessionState.READY

    def next(self, env: CompileEnv) -> SessionState:
        """Run the closure pipeline once (legal from new, ready and failed)."""
        from wynd.compiler import pipeline

        self._require("next", SessionState.NEW, SessionState.READY, SessionState.FAILED)
        self.data.state = SessionState.RUNNING
        state = SessionState(pipeline.compile_closure(self, env))
        self.data.state = state
        self.trim_memo()
        return state

    def cancel(self) -> None:
        self._require("cancel", SessionState.AWAITING_INPUT, SessionState.READY, SessionState.FAILED)
        self.data.state = SessionState.CANCELLED

    def ask(self, q: Question) -> Answer | None:
        """Pipeline-only; idempotent per question id and fingerprint ($DRAFTS/05 §6.4). The session sets the
        fingerprint, `asked_at` and `asked_in_job`. Returns the answer, or None while the question is pending."""
        job = self.data.jobs[-1] if self.data.jobs else self.data.id
        q = q.model_copy(update={
            "fingerprint": question_fingerprint(q.kind, q.text, q.proposed), "status": "pending", "answer": None,
            "answered_by": None, "asked_at": _now(), "asked_in_job": job,
        })
        existing = self._current(q.id)
        if existing is not None:
            if existing.fingerprint != q.fingerprint:
                existing.status = "stale"
                self.data.questions.append(q)
                self.emit("note", f"Question {q.id} changed; please answer again.", process=q.process, step=q.step)
                return None
            return existing.answer if existing.status == "answered" else None
        self.data.questions.append(q)
        self.data.report.questions.asked += 1
        if q.id in self.data.pending_answers:
            answer = interpret(q, self.data.pending_answers.pop(q.id))
            self._record(q, answer, "user")
            return answer
        if self.data.options.accept_proposals and q.kind == "example_proposal":
            answer = interpret(q, "accept")
            self._record(q, answer, "accept_proposals")
            return answer
        self.emit("question", q.text, process=q.process, step=q.step, data={"id": q.id})
        return None

    def emit(self, type: str, text: str, *, process: str, step: str | None, data: dict | None = None) -> None:
        """Pipeline-only: append a SessionEvent."""
        seq = self.data.events[-1].seq + 1 if self.data.events else 1
        self.data.events.append(SessionEvent(seq=seq, at=_now(), type=type, process=process, step=step, text=text,
                                             data=data or {}))

    def begin_job(self, job_id: str) -> None:
        """Note the job attempt that is about to run this session (the same id again when a job is requeued)."""
        if job_id not in self.data.jobs:
            self.data.jobs.append(job_id)
        self.data.report.jobs = list(self.data.jobs)

    def record_usage(self, *, kind: str, node: str, tier: str, usage: Usage) -> None:
        """Account one live compiler call: session totals, the node's report entry, by kind and by tier."""
        report = self.data.report
        self.data.usage = self.data.usage + usage
        report.usage.total = report.usage.total + usage
        report.usage.by_kind[kind] = report.usage.by_kind.get(kind, Usage()) + usage
        report.usage.by_tier[tier] = report.usage.by_tier.get(tier, Usage()) + usage
        process, _, local = node.rpartition(":")
        entry = report.step_entry(process or self.data.process, local)
        entry.usage.compiler = entry.usage.compiler + usage

    def trim_memo(self, limit: int = MEMO_LIMIT_BYTES) -> None:
        """Keep the memo under `limit` bytes of JSON: first drop entries of nodes already done (their steps are in a
        commit and will be skipped by hash), then the oldest entries."""
        memo = self.data.memo
        sizes = {(table, key): len(json.dumps(entry)) for table in ("calls", "tests")
                 for key, entry in getattr(memo, table).items()}
        total = len(json.dumps(memo.model_dump(mode="json")))
        if total <= limit:
            return
        done = {s.step for s in self.data.steps if s.phase in ("done", "skipped")}
        entries = list(sizes)
        order = [e for e in entries if getattr(memo, e[0])[e[1]].get("step") in done]
        order += [e for e in entries if e not in order]
        for table, key in order:
            if total <= limit:
                return
            del getattr(memo, table)[key]
            total -= sizes[(table, key)]

    def _require(self, action: str, *states: SessionState) -> None:
        if self.data.state not in states:
            legal = ", ".join(s.value for s in states)
            raise SessionStateError(f"cannot {action} a session that is {self.data.state.value} (legal: {legal})")

    def _current(self, question_id: str) -> Question | None:
        """The live (not stale) question with this id."""
        for q in self.data.questions:
            if q.id == question_id and q.status != "stale":
                return q
        return None

    def _record(self, q: Question, answer: Answer, by: Literal["user", "accept_proposals"]) -> None:
        first = q.status != "answered"
        q.status, q.answer, q.answered_by = "answered", answer, by
        if first:
            counts = self.data.report.questions
            if by == "user":
                counts.answered_by_user += 1
            else:
                counts.answered_by_accept_proposals += 1
        text = f"{q.id}: {answer.decision or 'answered'}" + (" (accepted by --accept-proposals)"
                                                              if by == "accept_proposals" else "")
        self.emit("answer", text, process=q.process, step=q.step, data={"id": q.id, "by": by})


def _example_mapping(text: str) -> dict | None:
    """A JSON or YAML mapping with a string `exit` (and mapping `inputs`/`outputs` when present), else None."""
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("exit"), str):
        return None
    if not all(isinstance(value.get(k, {}), dict) for k in ("inputs", "outputs")):
        return None
    return {"inputs": {}, "outputs": {}, **value}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

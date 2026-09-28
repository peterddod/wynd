"""Compile-session view (PLAN §8.1 compile_view row, §3.18 "Answering", §3.21 amendment 9; `$DRAFTS/06 §7.8`).

`session_dto` projects the compiler's `SessionData` JSON onto the web `CompileSession`; `answer_text` maps a web
answer to answer text (confirm -> "accept", reject -> "reject", correct+example -> `json.dumps(example)`,
text -> text); `apply_answers` uses `wynd.compiler.CompileSession.from_json(...).answer(...)`.

Nodes of child processes are named `<child-pid>:<node>` in the DTO, as the compiler names them in question ids and
`inferred_schemas` keys. Stale questions (superseded by a re-asked one with the same id) are not shown.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from wynd.compiler.session import CompileSession as Session
from wynd.compiler.session import SessionData, SessionState, SessionStateError
from wynd.controller.api.models_web import (
    ClarificationAnswer,
    ClarificationQuestion,
    CompileDecision,
    CompileEvent,
    CompileSession,
    CompileSplit,
    CompileStep,
    DecisionAnswer,
    ExampleProposalQuestion,
    ProposalAnswer,
    ProposedExample,
    TextAnswer,
)
from wynd.controller.errors import Invalid, JobState
from wynd.controller.models import ExitSchema, Interface, PassCounts
from wynd.spec.base import RESERVED_EXIT

if TYPE_CHECKING:
    from wynd.compiler import session as compiler
    from wynd.controller.api.models_web import AnswerRequest
    from wynd.process.jobs import JobRecord

STATES = {
    SessionState.NEW: "compiling",
    SessionState.RUNNING: "compiling",
    SessionState.READY: "compiling",
    SessionState.AWAITING_INPUT: "awaiting_input",
    SessionState.DONE: "done",
    SessionState.FAILED: "failed",
    SessionState.CANCELLED: "failed",
}


def session_dto(session: dict | None) -> CompileSession | None:
    if session is None:
        return None
    data = SessionData.model_validate(session)
    return CompileSession(
        state=STATES[data.state],
        steps=[_step(data.process, s) for s in data.steps],
        questions=[_question(data.process, q) for q in data.questions if q.status != "stale"],
        inferred_schemas={node: _inferred(entry) for node, entry in data.inferred_schemas.items()},
        events=[CompileEvent(at=e.at, type=e.type, step=_node(data.process, e.process, e.step), text=e.text)
                for e in data.events],
    )


def answer_text(question: dict, answer: AnswerRequest) -> str:
    """`question` (the compiler question the answer is for) is not consulted: every mapping is by answer form."""
    match answer:
        case TextAnswer(text=text):
            return text
        case DecisionAnswer(decision="confirm"):
            return "accept"
        case DecisionAnswer(decision="reject"):
            return "reject"
        case DecisionAnswer(decision="correct", example=example) if example is not None:
            return json.dumps(example)
        case DecisionAnswer(decision="correct", text=text) if text:
            return text
    raise Invalid(f"answer to {answer.question_id}: a correction needs an example or a text")


def apply_answers(job: JobRecord, answers: Sequence[AnswerRequest] | Mapping[str, str]) -> tuple[dict, bool]:
    """-> (the new session JSON, whether it is ready to resume). A mapping is `{question id: answer text}` (CLI);
    an id not asked yet is kept by the compiler as a pre-answer."""
    if job.session is None:
        raise JobState(f"job {job.id} has no compile session to answer")
    session = Session.from_json(job.session)
    questions = {q.id: q.model_dump(mode="json") for q in session.data.questions if q.status != "stale"}
    if isinstance(answers, Mapping):
        texts = {str(qid): str(text) for qid, text in answers.items()}
    else:
        texts = {a.question_id: answer_text(questions.get(a.question_id, {}), a) for a in answers}
    try:
        for qid, text in texts.items():
            session.answer(qid, text)
    except SessionStateError as err:
        raise JobState(f"job {job.id}: {err}") from None
    return session.to_json(), session.state == SessionState.READY


def _node(root: str, process: str, node: str | None) -> str | None:
    if node is None or process == root:
        return node
    return f"{process}:{node}"


def _step(root: str, s: compiler.CompileStep) -> CompileStep:
    decision = None
    if s.decision is not None:
        d = s.decision
        split = None if d.split is None else CompileSplit(deterministic=d.split["deterministic"],
                                                          agentic=d.split["agentic"])
        decision = CompileDecision(kind=d.kind, tier=d.tier, thinking=d.thinking, reason=d.reason, split=split)
    tests = None if s.tests is None else PassCounts(passed=s.tests.passed, failed=s.tests.failed, total=s.tests.total)
    return CompileStep(
        step=_node(root, s.process, s.step),
        use=s.use,
        phase="pending" if s.phase == "awaiting_input" else s.phase,
        decision=decision,
        tests=tests,
        attempts=s.attempts,
        skipped_reason=s.skipped_reason,
    )


def _question(root: str, q: compiler.Question) -> ClarificationQuestion | ExampleProposalQuestion:
    step = _node(root, q.process, q.step)
    status = "answered" if q.status == "answered" else "pending"
    common = dict(id=q.id, step=step, text=q.text, status=status, asked_at=q.asked_at, detail=q.detail or None,
                  default=q.default)
    if q.kind == "clarification":
        answer = None if q.answer is None else ClarificationAnswer(text=q.answer.text)
        return ClarificationQuestion(**common, answer=answer)
    answer = None
    if q.answer is not None:
        answer = ProposalAnswer(decision=q.answer.decision or "correct", example=q.answer.example,
                                text=q.answer.text)
    return ExampleProposalQuestion(**common, proposed=ProposedExample.model_validate(q.proposed), answer=answer)


def _inferred(entry: dict) -> Interface:
    """`{"interface": spec Interface JSON, "plain": [...]}` -> the web `Interface` (the implicit error exit dropped)."""
    iface = entry["interface"]
    exits = [ExitSchema(name=name, schema=schema) for name, schema in iface["outputs"].items() if name != RESERVED_EXIT]
    return Interface(inputs=iface["input"], exits=exits, source="inferred")

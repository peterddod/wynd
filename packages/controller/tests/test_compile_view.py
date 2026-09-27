"""`compile_view` (PLAN §8.1, §3.18 "Answering", §3.21 amendment 9; `$DRAFTS/06 §7.8`): projection of a compiler
session into the web `CompileSession` (states, phases, decisions incl. split, question kinds, stale questions, child
process nodes, inferred schemas, events), `answer_text` for every `AnswerRequest` form, and `apply_answers` over the
real compiler session (partial, complete, CLI mapping, pre-answer, re-asked question, illegal state)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from wynd.compiler.session import (
    CompileOptions,
    CompileStep,
    Question,
    SessionState,
    StepDecision,
    TestCounts,
)
from wynd.compiler.session import CompileSession as Session
from wynd.controller.api.models_web import (
    ClarificationQuestion,
    CompileSession,
    DecisionAnswer,
    ExampleProposalQuestion,
    TextAnswer,
)
from wynd.controller.compile_view import answer_text, apply_answers, session_dto
from wynd.controller.errors import Invalid, JobState
from wynd.process.jobs import JobRecord
from wynd.spec.interface import interface_from_fields
from wynd.spec.typelang import parse_type

ROOT = "invoices"
PROPOSED = {"inputs": {"invoice_text": "INVOICE INV-2001"}, "exit": "not_an_invoice", "outputs": {}}
CORRECTED = {"inputs": {"invoice_text": "INVOICE INV-2001"}, "exit": "done", "outputs": {"total": 80}}


def proposal(qid: str = "extract.example1", *, process: str = ROOT, text: str = "What if there is no due date?",
             proposed: dict = PROPOSED) -> Question:
    return Question(id=qid, kind="example_proposal", process=process, step="extract",
                    package="extract_invoice_fields", text=text,
                    detail="Why: without a due date it may be a quote.", proposed=proposed, expects="decision",
                    default="accept", fingerprint="", asked_at="", asked_in_job="")


def clarification(qid: str = "fix.clarify1", *, process: str = ROOT) -> Question:
    return Question(id=qid, kind="clarification", process=process, step="fix", package="fix_fields",
                    text="Which currency when none is given?", fingerprint="", asked_at="", asked_in_job="")


def step(node: str, phase: str, *, process: str = ROOT, decision: StepDecision | None = None,
         tests: TestCounts | None = None, attempts: int = 0, skipped_reason: str | None = None) -> CompileStep:
    return CompileStep(process=process, step=node, use=f"./steps/{node}", package=node, phase=phase,
                       decision=decision, tests=tests, attempts=attempts, skipped_reason=skipped_reason)


def asking(*questions: Question) -> Session:
    """A session of job `job_1` that asked `questions` and stopped awaiting input."""
    s = Session.new(session_id="job_1", process=ROOT, base_commit="b" * 40, options=CompileOptions())
    s.begin_job("job_1")
    for q in questions:
        assert s.ask(q) is None
    s.data.state = SessionState.AWAITING_INPUT
    return s


def example_session() -> Session:
    """The `$DRAFTS/05 §6.5` example, extended with a split decision, a child process node, a clarification, a stale
    question and an inferred schema."""
    s = asking(proposal(), clarification(), proposal("sub:read.example1", process="sub", text="Empty file?"))
    s.ask(proposal(text="What if the due date is missing?"))   # re-asked with a new text: the first becomes stale
    s.data.steps = [
        step("read", "done", attempts=1, tests=TestCounts(passed=3, failed=0, total=3),
             decision=StepDecision(kind="deterministic", rule=1, tier=None, thinking=None,
                                   reason="Extracting the text layer is a pure function of the file.")),
        step("extract", "awaiting_input"),
        step("fix", "revising", attempts=2,
             decision=StepDecision(kind="agentic", rule=3, tier="cheap", thinking="low", reason="Mostly rules.",
                                   split={"deterministic": "fix", "agentic": "fix_agentic", "handled": [1, 2],
                                          "deferred": [3]})),
        step("save", "skipped", skipped_reason="proto-step unchanged"),
        step("read", "generating", process="sub"),
    ]
    obj, text = parse_type("object"), parse_type("string")
    iface = interface_from_fields({"fields": obj}, {"done": {"fields": obj}, "cannot_fix": {"why": text}})
    s.data.inferred_schemas["fix"] = {"interface": iface.model_dump(mode="json"), "plain": ["fix takes: fields"]}
    return s


def compile_job(session: Session, status: str = "awaiting_input") -> JobRecord:
    now = datetime.now(UTC)
    return JobRecord(id="job_1", job_kind="compile", process=ROOT, ref="c" * 40, base_commit="b" * 40,
                     target_branch="main", inputs={"process": ROOT, "target_branch": "main", "answers": {}},
                     status=status, runner="inprocess", handler="h:h", created_at=now, updated_at=now,
                     session=session.to_json())


def by_id(dto: CompileSession) -> dict[str, ClarificationQuestion | ExampleProposalQuestion]:
    return {q.id: q for q in dto.questions}


# --- session_dto --------------------------------------------------------------------------------------------------

def test_no_session() -> None:
    assert session_dto(None) is None


@pytest.mark.parametrize(("state", "web"), [
    (SessionState.NEW, "compiling"), (SessionState.RUNNING, "compiling"), (SessionState.READY, "compiling"),
    (SessionState.AWAITING_INPUT, "awaiting_input"), (SessionState.DONE, "done"), (SessionState.FAILED, "failed"),
    (SessionState.CANCELLED, "failed"),
])
def test_states(state: SessionState, web: str) -> None:
    s = example_session()
    s.data.state = state
    assert session_dto(s.to_json()).state == web


def test_steps() -> None:
    dto = session_dto(example_session().to_json())
    rows = {(r.step, r.phase): r for r in dto.steps}
    assert list(rows) == [("read", "done"), ("extract", "pending"), ("fix", "revising"), ("save", "skipped"),
                          ("sub:read", "generating")]
    read = rows["read", "done"]
    assert read.use == "./steps/read" and read.attempts == 1
    assert read.tests.model_dump() == {"passed": 3, "failed": 0, "total": 3}
    assert read.decision.model_dump() == {"kind": "deterministic", "tier": None, "thinking": None, "split": None,
                                          "reason": "Extracting the text layer is a pure function of the file."}
    fix = rows["fix", "revising"].decision
    assert (fix.kind, fix.tier, fix.thinking) == ("agentic", "cheap", "low")
    assert fix.split.model_dump() == {"deterministic": "fix", "agentic": "fix_agentic"}
    assert rows["extract", "pending"].decision is None and rows["extract", "pending"].tests is None
    assert rows["save", "skipped"].skipped_reason == "proto-step unchanged"


def test_questions() -> None:
    s = example_session()
    dto = session_dto(s.to_json())
    questions = by_id(dto)
    assert list(questions) == ["fix.clarify1", "sub:read.example1", "extract.example1"]   # the stale one is gone

    extract = questions["extract.example1"]
    assert isinstance(extract, ExampleProposalQuestion)
    assert extract.text == "What if the due date is missing?" and extract.step == "extract"
    assert extract.proposed.model_dump() == PROPOSED
    assert (extract.status, extract.answer, extract.default) == ("pending", None, "accept")
    assert extract.detail == "Why: without a due date it may be a quote."
    asked = next(q for q in s.data.questions if q.id == "extract.example1" and q.status == "pending").asked_at
    assert extract.asked_at == datetime.fromisoformat(asked)

    clarify = questions["fix.clarify1"]
    assert isinstance(clarify, ClarificationQuestion)
    assert (clarify.step, clarify.status, clarify.detail, clarify.default) == ("fix", "pending", None, None)
    assert questions["sub:read.example1"].step == "sub:extract"


def test_answered_questions() -> None:
    s = example_session()
    s.answer("fix.clarify1", "GBP for UK suppliers")
    s.answer("extract.example1", "reject")
    s.answer("sub:read.example1", json_text := '{"exit": "done", "inputs": {}, "outputs": {"total": 80}}')
    questions = by_id(session_dto(s.to_json()))
    assert questions["fix.clarify1"].status == "answered"
    assert questions["fix.clarify1"].answer.model_dump() == {"text": "GBP for UK suppliers"}
    assert questions["extract.example1"].answer.model_dump() == {"decision": "reject", "example": None,
                                                                  "text": "reject"}
    corrected = questions["sub:read.example1"].answer
    assert (corrected.decision, corrected.text) == ("correct", json_text)
    assert corrected.example == {"exit": "done", "inputs": {}, "outputs": {"total": 80}}


def test_inferred_schemas_and_events() -> None:
    dto = session_dto(example_session().to_json())
    fix = dto.inferred_schemas["fix"]
    assert fix.source == "inferred"
    assert fix.inputs["required"] == ["fields"]
    assert [e.name for e in fix.exits] == ["done", "cannot_fix"]
    assert fix.exits[1].schema_["properties"]["why"]["type"] == "string"

    kinds = [(e.type, e.step) for e in dto.events]
    assert ("question", "extract") in kinds and ("question", "sub:extract") in kinds
    assert ("note", "extract") in kinds                          # "question changed; please answer again"
    assert all(isinstance(e.at, datetime) for e in dto.events)
    # the DTO is what the Job DTO carries: it survives a JSON round trip through the web model
    assert CompileSession.model_validate(dto.model_dump(mode="json")) == dto


# --- answer_text ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("answer", "text"), [
    (TextAnswer(question_id="q", text="GBP only"), "GBP only"),
    (DecisionAnswer(question_id="q", decision="confirm"), "accept"),
    (DecisionAnswer(question_id="q", decision="confirm", text="looks right"), "accept"),
    (DecisionAnswer(question_id="q", decision="reject"), "reject"),
    (DecisionAnswer(question_id="q", decision="reject", text="not a real case"), "reject"),
    (DecisionAnswer(question_id="q", decision="correct", example=CORRECTED),
     '{"inputs": {"invoice_text": "INVOICE INV-2001"}, "exit": "done", "outputs": {"total": 80}}'),
    (DecisionAnswer(question_id="q", decision="correct", example=CORRECTED, text="ignored"),
     '{"inputs": {"invoice_text": "INVOICE INV-2001"}, "exit": "done", "outputs": {"total": 80}}'),
    (DecisionAnswer(question_id="q", decision="correct", text="It should be not_an_invoice."),
     "It should be not_an_invoice."),
])
def test_answer_text(answer, text: str) -> None:
    assert answer_text(proposal().model_dump(mode="json"), answer) == text


def test_a_correction_needs_an_example_or_text() -> None:
    with pytest.raises(Invalid):
        answer_text({}, DecisionAnswer(question_id="extract.example1", decision="correct"))


def test_the_compiler_reads_a_correction_as_the_example() -> None:
    s = asking(proposal())
    session, ready = apply_answers(compile_job(s), [
        DecisionAnswer(question_id="extract.example1", decision="correct", example=CORRECTED)])
    answer = Session.from_json(session).data.questions[0].answer
    assert ready and (answer.decision, answer.example) == ("correct", CORRECTED)


# --- apply_answers --------------------------------------------------------------------------------------------------

def test_partial_answers_stay_awaiting_input() -> None:
    job = compile_job(asking(proposal(), clarification()))
    session, ready = apply_answers(job, [DecisionAnswer(question_id="extract.example1", decision="confirm")])
    assert not ready
    assert session["state"] == "awaiting_input"
    statuses = {q["id"]: (q["status"], q["answered_by"]) for q in session["questions"]}
    assert statuses == {"extract.example1": ("answered", "user"), "fix.clarify1": ("pending", None)}
    assert session["questions"][0]["answer"]["example"] == PROPOSED
    assert job.session["questions"][0]["status"] == "pending"          # the record is not mutated in place


def test_last_answer_makes_the_session_ready() -> None:
    job = compile_job(asking(proposal(), clarification()))
    session, ready = apply_answers(job, [TextAnswer(question_id="fix.clarify1", text="GBP"),
                                         DecisionAnswer(question_id="extract.example1", decision="reject")])
    assert ready and session["state"] == "ready"
    answers = {q["id"]: q["answer"] for q in session["questions"]}
    assert answers["fix.clarify1"]["text"] == "GBP"
    assert answers["extract.example1"]["decision"] == "reject"
    assert session_dto(session).state == "compiling"


def test_cli_mapping_and_pre_answers() -> None:
    job = compile_job(asking(proposal()))
    session, ready = apply_answers(job, {"extract.example1": "yes", "fix.clarify1": "GBP"})
    assert ready
    assert session["pending_answers"] == {"fix.clarify1": "GBP"}          # kept for when it is asked
    assert session["questions"][0]["answer"]["decision"] == "confirm"


def test_answers_go_to_the_re_asked_question() -> None:
    s = asking(proposal())
    s.ask(proposal(text="Changed question?"))
    session, ready = apply_answers(compile_job(s), {"extract.example1": "accept"})
    assert ready
    assert [(q["text"], q["status"]) for q in session["questions"]] == [
        ("What if there is no due date?", "stale"), ("Changed question?", "answered")]


def test_answering_a_finished_session_is_a_job_state_error() -> None:
    s = asking(proposal())
    s.data.state = SessionState.DONE
    with pytest.raises(JobState):
        apply_answers(compile_job(s), {"extract.example1": "accept"})


def test_a_job_without_session_is_a_job_state_error() -> None:
    job = compile_job(asking(proposal())).model_copy(update={"session": None})
    with pytest.raises(JobState):
        apply_answers(job, {"extract.example1": "accept"})

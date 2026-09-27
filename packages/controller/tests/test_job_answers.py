"""Answering a compile job end to end (PLAN §3.18 "Answering", §15 item 43): `JobService.answer` over a real
in-process runner and the real `compile_view`, with a handler that keeps a real compiler session. Partial answers
update the record and leave the job awaiting input; the last answer requeues the same job (same id, attempt 2,
cumulative answers, checkout at the WIP commit), whose resumed attempt re-applies the answers to its session."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from support.ctl_jobs_workspace import make_context, make_runner, make_workspace, run_git, settle
from wynd.compiler.session import CompileOptions, Question, SessionState
from wynd.compiler.session import CompileSession as Session
from wynd.controller.api.models_web import CompileSession, DecisionAnswer, TextAnswer
from wynd.controller.errors import Invalid, JobState
from wynd.controller.jobs.inprocess import InProcessJobRunner
from wynd.controller.jobs.service import JobService
from wynd.process.jobs import JobContext, JobOutcome

HANDLERS_MODULE = "ctl_m3_answer_handlers"
PROPOSED = {"inputs": {"text": ""}, "exit": "done", "outputs": {"text": ""}}
CORRECTED = {"inputs": {"text": ""}, "exit": "done", "outputs": {"text": "EMPTY"}}


def questions(pid: str) -> list[Question]:
    return [
        Question(id="read.example1", kind="example_proposal", process=pid, step="read", package="read",
                 text="What if the text is empty?", proposed=PROPOSED, expects="decision", default="accept",
                 fingerprint="", asked_at="", asked_in_job=""),
        Question(id="write.clarify1", kind="clarification", process=pid, step="write", package="write",
                 text="Where should the text go?", fingerprint="", asked_at="", asked_in_job=""),
    ]


def ask_twice(ctx: JobContext) -> JobOutcome:
    """First attempt: a real session asks two questions over a WIP commit. Resumed attempt: re-apply the cumulative
    answers (as `run_compile_job` does) and finish with a commit recording what the session saw."""
    pid = ctx.inputs["process"]
    if ctx.session is None:
        session = Session.new(session_id=ctx.job.id, process=pid, base_commit=ctx.job.base_commit,
                              options=CompileOptions())
        session.begin_job(ctx.job.id)
        for q in questions(pid):
            session.ask(q)
        session.data.state = SessionState.AWAITING_INPUT
        (ctx.workspace / f"processes/{pid}/WIP.md").write_text("work in progress\n")
        wip = ctx.commit("wip", None)
        data = session.to_json()
        return JobOutcome(status="awaiting_input", commit=wip, session=data,
                          questions=[q.model_dump(mode="json") for q in session.pending_questions])
    session = Session.from_json(ctx.session)
    session.begin_job(ctx.job.id)
    session.apply_answers(ctx.inputs["answers"], accept_proposals=False)
    state_on_resume = session.state.value
    wip_present = (ctx.workspace / f"processes/{pid}/WIP.md").exists()
    session.data.state = SessionState.DONE
    (ctx.workspace / f"processes/{pid}/DONE.md").write_text("done\n")
    sha = ctx.commit("done", None)
    answers = {q.id: q.answer.model_dump(mode="json") for q in session.data.questions}
    return JobOutcome(status="succeeded", commit=sha, session=session.to_json(),
                      report={"state_on_resume": state_on_resume, "wip_present": wip_present,
                              "attempt": ctx.job.attempt, "answers": answers})


@pytest.fixture(autouse=True)
def handler_module(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType(HANDLERS_MODULE)
    module.ask_twice = ask_twice
    monkeypatch.setitem(sys.modules, HANDLERS_MODULE, module)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return make_workspace(tmp_path)


@pytest.fixture
def service(ws: Path) -> JobService:
    handlers = {kind: f"{HANDLERS_MODULE}:ask_twice" for kind in ("compile", "test_live", "build", "bake")}
    runner = make_runner(ws, InProcessJobRunner, handlers)
    return JobService(make_context(ws, runner), ctl=None)


@pytest.fixture
def waiting(service: JobService) -> str:
    job = service.submit_compile("alpha")
    record = settle(service.ctx.runner, job.id)
    assert record.status == "awaiting_input"
    return job.id


def test_the_waiting_job_shows_its_session(service: JobService, waiting: str) -> None:
    job = service.get(waiting)
    dto = CompileSession.model_validate(job.session)
    assert dto.state == "awaiting_input"
    assert [(q.id, q.kind, q.status) for q in dto.questions] == [
        ("read.example1", "example_proposal", "pending"), ("write.clarify1", "clarification", "pending")]
    record = service.ctx.runner.status(waiting)
    assert [q["id"] for q in record.questions] == ["read.example1", "write.clarify1"]


def test_partial_then_complete_answers_requeue_the_same_job(ws: Path, service: JobService, waiting: str) -> None:
    runner = service.ctx.runner
    wip = runner.status(waiting).result_commit
    assert wip is not None

    job = service.answer(waiting, [DecisionAnswer(question_id="read.example1", decision="correct",
                                                  example=CORRECTED)])
    assert job.status == "awaiting_input"
    record = runner.status(waiting)
    assert record.attempt == 1 and record.inputs["answers"] == {}
    assert [q["id"] for q in record.questions] == ["write.clarify1"]
    dto = CompileSession.model_validate(job.session)
    read = next(q for q in dto.questions if q.id == "read.example1")
    assert read.status == "answered" and (read.answer.decision, read.answer.example) == ("correct", CORRECTED)

    job = service.answer(waiting, [TextAnswer(question_id="write.clarify1", text="Into the notes file.")])
    assert job.id == waiting
    record = settle(runner, waiting)
    assert record.status == "succeeded" and record.attempt == 2
    assert record.ref == wip                                   # the requeued checkout is at the WIP commit
    assert record.inputs["answers"] == {"write.clarify1": "Into the notes file."}
    assert record.report["state_on_resume"] == "ready" and record.report["wip_present"] is True
    assert record.report["answers"]["read.example1"]["example"] == CORRECTED
    assert record.report["answers"]["write.clarify1"]["text"] == "Into the notes file."
    assert CompileSession.model_validate(service.get(waiting).session).state == "done"
    assert run_git(ws, "rev-parse", "HEAD") == record.base_commit          # the user's branch is untouched


def test_answers_are_cumulative_in_the_job_inputs(service: JobService, waiting: str) -> None:
    runner = service.ctx.runner
    service.answer(waiting, {"read.example1": "accept", "write.clarify1": "Into the notes file."})
    record = settle(runner, waiting)
    assert record.status == "succeeded"
    assert record.inputs["answers"] == {"read.example1": "accept", "write.clarify1": "Into the notes file."}
    assert record.report["answers"]["read.example1"]["decision"] == "confirm"
    assert record.report["answers"]["read.example1"]["example"] == PROPOSED


def test_only_a_waiting_compile_job_takes_answers(service: JobService, waiting: str) -> None:
    service.answer(waiting, {"read.example1": "reject", "write.clarify1": "Anywhere."})
    assert settle(service.ctx.runner, waiting).status == "succeeded"
    with pytest.raises(JobState):
        service.answer(waiting, {"read.example1": "accept"})


def test_a_bad_correction_changes_nothing(service: JobService, waiting: str) -> None:
    before = service.ctx.runner.status(waiting)
    with pytest.raises(Invalid):
        service.answer(waiting, [DecisionAnswer(question_id="read.example1", decision="correct")])
    after = service.ctx.runner.status(waiting)
    assert after.session == before.session and after.status == "awaiting_input"

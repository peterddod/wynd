"""The compile session (SPEC §9; `$DRAFTS/05 §6`): state machine, JSON round trip, `ask()` idempotence and stale
fingerprints, pre-answers, `accept_proposals`, the answer-interpretation table, question ids, usage and the memo cap."""

import json

import pytest

from wynd.compiler import pipeline
from wynd.compiler.session import (
    Answer,
    CompileOptions,
    CompileSession,
    CompileStep,
    Question,
    SessionData,
    SessionState,
    SessionStateError,
    interpret,
    question_fingerprint,
    question_id,
)
from wynd.runtime.usage import Usage

PROPOSED = {"inputs": {"invoice_text": "INVOICE INV-2001"}, "exit": "not_an_invoice", "outputs": {}}
S = SessionState


def new_session(**options) -> CompileSession:
    s = CompileSession.new(session_id="job_1", process="invoices", base_commit="b" * 40,
                           options=CompileOptions(**options))
    s.begin_job("job_1")
    return s


def proposal(qid: str = "extract.example1", text: str = "What if there is no due date?", proposed=PROPOSED) -> Question:
    return Question(id=qid, kind="example_proposal", process="invoices", step="extract",
                    package="extract_invoice_fields", text=text, proposed=proposed, expects="decision",
                    default="accept", fingerprint="", asked_at="", asked_in_job="")


def clarification(qid: str = "fix.clarify1", text: str = "Which currency when none is given?") -> Question:
    return Question(id=qid, kind="clarification", process="invoices", step="fix", package="fix_fields", text=text,
                    fingerprint="", asked_at="", asked_in_job="")


def in_state(state: SessionState) -> CompileSession:
    s = new_session()
    s.data.state = state
    return s


def fake_pipeline(monkeypatch, result: SessionState, seen: list | None = None) -> None:
    def compile_closure(session, env):
        if seen is not None:
            seen.append((session.state, env))
        return result

    monkeypatch.setattr(pipeline, "compile_closure", compile_closure)


# --- construction and JSON ------------------------------------------------------------------------------------------

def test_a_new_session_carries_its_identity_into_the_report():
    s = new_session(accept_proposals=True, max_revisions=2)
    assert (s.state, s.pending_questions, s.data.jobs) == (S.NEW, [], ["job_1"])
    report = s.data.report
    assert (report.process, report.session, report.base_commit, report.jobs) == ("invoices", "job_1", "b" * 40,
                                                                                ["job_1"])
    assert s.data.options == CompileOptions(accept_proposals=True, max_revisions=2, max_proposals=3)


def test_the_json_has_the_documented_shape():
    data = new_session().to_json()
    assert list(data) == ["version", "id", "process", "base_commit", "state", "options", "jobs", "steps", "questions",
                          "inferred_schemas", "events", "pending_answers", "memo", "usage", "report"]
    assert (data["version"], data["id"], data["state"]) == (1, "job_1", "new")
    assert data["options"] == {"accept_proposals": False, "max_revisions": 3, "max_proposals": 3}
    assert data["memo"] == {"calls": {}, "tests": {}}
    assert data["report"]["report_version"] == 1
    json.dumps(data)                                         # plain JSON, ready for JobRecord.session


def test_json_round_trip_keeps_everything():
    s = new_session()
    s.answer("later.example1", "reject")
    s.data.state = S.AWAITING_INPUT
    s.ask(proposal())
    s.ask(clarification())
    s.data.steps.append(CompileStep(process="invoices", step="read", use="./steps/read_pdf", package="read_pdf",
                                    phase="done"))
    s.data.inferred_schemas["fix"] = {"interface": {"input": {}, "outputs": {}}, "plain": ["fix takes:"]}
    s.data.memo.calls["k1"] = {"kind": "decide", "step": "read", "response": {"a": 1}, "usage": {}}
    s.record_usage(kind="decide", node="read", tier="strong", usage=Usage(input_tokens=10, calls=1, cost_usd=0.1))
    back = CompileSession.from_json(json.loads(json.dumps(s.to_json())))
    assert back.data == s.data
    assert back.to_json() == s.to_json()


def test_options_come_from_job_inputs():
    assert CompileOptions.from_inputs({"process": "p", "answers": {}}) == CompileOptions()
    assert CompileOptions.from_inputs({"accept_proposals": True, "max_revisions": 5, "max_proposals": None}) == \
        CompileOptions(accept_proposals=True, max_revisions=5)


# --- state machine --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("start", [S.NEW, S.READY, S.FAILED])
@pytest.mark.parametrize("result", [S.DONE, S.FAILED, S.AWAITING_INPUT])
def test_next_runs_the_pipeline_from_new_ready_and_failed(monkeypatch, start, result):
    seen = []
    fake_pipeline(monkeypatch, result, seen)
    s = in_state(start)
    assert s.next("the env") == result
    assert s.state == result
    assert seen == [(S.RUNNING, "the env")]                 # running while the pipeline works


@pytest.mark.parametrize("start", [S.RUNNING, S.AWAITING_INPUT, S.DONE, S.CANCELLED])
def test_next_is_illegal_elsewhere(monkeypatch, start):
    fake_pipeline(monkeypatch, S.DONE)
    s = in_state(start)
    with pytest.raises(SessionStateError, match=f"cannot next a session that is {start.value}"):
        s.next(None)
    assert s.state == start


@pytest.mark.parametrize("start", [S.NEW, S.AWAITING_INPUT, S.READY])
def test_answer_is_legal_in_new_awaiting_and_ready(start):
    s = in_state(start)
    s.answer("x.example1", "accept")
    assert s.data.pending_answers == {"x.example1": "accept"}
    assert s.state == (S.NEW if start == S.NEW else S.READY)


def test_a_failed_session_takes_its_cumulative_answers_again():
    s = in_state(S.RUNNING)
    s.ask(proposal())
    s.data.state = S.AWAITING_INPUT
    s.answer("extract.example1", "yes")
    s.data.state = S.FAILED                                  # e.g. a later replay failed
    s.apply_answers({"extract.example1": "yes"}, accept_proposals=True)
    assert s.state == S.FAILED                               # nothing new: still resumable by next()
    s.answer("extract.example1", "reject")                   # a changed answer is recorded and makes it ready
    assert (s.state, s.data.questions[0].answer.decision) == (S.READY, "reject")


@pytest.mark.parametrize("start", [S.RUNNING, S.DONE, S.CANCELLED])
def test_answer_is_illegal_elsewhere(start):
    with pytest.raises(SessionStateError, match="cannot answer"):
        in_state(start).answer("x.example1", "accept")


@pytest.mark.parametrize("start", [S.AWAITING_INPUT, S.READY, S.FAILED])
def test_cancel_is_legal_from_awaiting_ready_and_failed(start):
    s = in_state(start)
    s.cancel()
    assert s.state == S.CANCELLED


@pytest.mark.parametrize("start", [S.NEW, S.RUNNING, S.DONE, S.CANCELLED])
def test_cancel_is_illegal_elsewhere(start):
    with pytest.raises(SessionStateError, match="cannot cancel"):
        in_state(start).cancel()


def test_answering_every_pending_question_makes_the_session_ready():
    s = in_state(S.RUNNING)
    s.ask(proposal())
    s.ask(clarification())
    s.data.state = S.AWAITING_INPUT
    assert [q.id for q in s.pending_questions] == ["extract.example1", "fix.clarify1"]
    s.answer("extract.example1", "yes")
    assert s.state == S.AWAITING_INPUT                      # a partial answer just updates the record
    s.answer("fix.clarify1", "Assume GBP for UK suppliers.")
    assert (s.state, s.pending_questions) == (S.READY, [])
    s.answer("fix.clarify1", "Escalate instead.")           # still answerable while ready
    assert s.state == S.READY
    assert s.data.questions[1].answer == Answer(text="Escalate instead.")


# --- ask() ----------------------------------------------------------------------------------------------------------

def test_ask_records_a_pending_question_once():
    s = in_state(S.RUNNING)
    assert s.ask(proposal()) is None
    assert s.ask(proposal()) is None                        # re-execution asks the same question again
    [q] = s.data.questions
    assert (q.status, q.asked_in_job, q.fingerprint) == ("pending", "job_1",
                                                         question_fingerprint("example_proposal", q.text, PROPOSED))
    assert q.asked_at.endswith("Z")
    assert [(e.type, e.text, e.data) for e in s.data.events] == [
        ("question", "What if there is no due date?", {"id": "extract.example1"})]
    assert s.data.report.questions.asked == 1


def test_ask_returns_the_recorded_answer_when_the_fingerprint_matches():
    s = in_state(S.RUNNING)
    s.ask(proposal())
    s.data.state = S.AWAITING_INPUT
    s.answer("extract.example1", "reject")
    s.data.state = S.RUNNING
    assert s.ask(proposal()) == Answer(text="reject", decision="reject")
    assert len(s.data.questions) == 1


def test_a_changed_question_goes_stale_and_is_asked_again():
    s = in_state(S.RUNNING)
    s.ask(proposal())
    s.data.state = S.AWAITING_INPUT
    s.answer("extract.example1", "accept")
    s.data.state = S.RUNNING
    changed = proposal(proposed={**PROPOSED, "exit": "done"})
    assert s.ask(changed) is None
    old, new = s.data.questions
    assert (old.status, new.status, new.proposed["exit"]) == ("stale", "pending", "done")
    assert s.data.events[-1].type == "note" and "extract.example1 changed" in s.data.events[-1].text
    assert [q.id for q in s.pending_questions] == ["extract.example1"]
    s.data.state = S.AWAITING_INPUT
    s.answer("extract.example1", "yes")                     # the answer goes to the live question
    assert (old.answer.decision, new.answer.example) == ("confirm", {**PROPOSED, "exit": "done"})


def test_pre_answers_are_used_when_the_question_is_asked():
    s = new_session()
    s.apply_answers({"extract.example1": "not a real case", "fix.clarify1": "GBP"}, accept_proposals=False)
    assert s.state == S.NEW
    s.data.state = S.RUNNING
    assert s.ask(proposal()) == Answer(text="not a real case", decision="reject")
    assert s.ask(clarification()) == Answer(text="GBP")
    assert s.data.pending_answers == {}
    assert [q.answered_by for q in s.data.questions] == ["user", "user"]
    assert s.data.report.questions.answered_by_user == 2
    assert not [e for e in s.data.events if e.type == "question"]


def test_accept_proposals_answers_proposals_but_never_clarifications():
    s = new_session(accept_proposals=True)
    s.data.state = S.RUNNING
    assert s.ask(proposal()) == Answer(text="accept", decision="confirm", example=PROPOSED)
    assert s.ask(clarification()) is None
    assert [(q.status, q.answered_by) for q in s.data.questions] == [("answered", "accept_proposals"),
                                                                      ("pending", None)]
    counts = s.data.report.questions
    assert (counts.asked, counts.answered_by_accept_proposals, counts.answered_by_user) == (2, 1, 0)


def test_apply_answers_with_accept_proposals_answers_the_pending_proposals():
    s = in_state(S.RUNNING)
    s.ask(proposal())
    s.ask(proposal("extract.example2", "What about a credit note?", {"inputs": {}, "exit": "done", "outputs": {}}))
    s.ask(clarification())
    s.data.state = S.AWAITING_INPUT
    s.apply_answers({"extract.example2": "reject"}, accept_proposals=True)
    assert s.data.options.accept_proposals
    assert [(q.id, q.answer and q.answer.decision, q.answered_by) for q in s.data.questions] == [
        ("extract.example1", "confirm", "accept_proposals"), ("extract.example2", "reject", "user"),
        ("fix.clarify1", None, None)]
    assert s.state == S.AWAITING_INPUT
    s.apply_answers({"fix.clarify1": "GBP"}, accept_proposals=True)
    assert s.state == S.READY


def test_reapplying_the_same_answers_changes_nothing():
    s = in_state(S.RUNNING)
    s.ask(clarification())
    s.data.state = S.AWAITING_INPUT
    s.apply_answers({"fix.clarify1": "GBP"}, accept_proposals=False)
    events = len(s.data.events)
    s.apply_answers({"fix.clarify1": "GBP"}, accept_proposals=False)    # a requeued attempt gets them again
    assert len(s.data.events) == events
    assert s.data.report.questions.answered_by_user == 1


# --- interpretation and ids -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "accept", "Confirm", " yes ", "Y"])
def test_accepting_texts_confirm_the_proposed_example(text):
    assert interpret(proposal(), text) == Answer(text=text, decision="confirm", example=PROPOSED)


@pytest.mark.parametrize("text", ["reject", "No", "skip", "not a real case"])
def test_rejecting_texts_reject(text):
    assert interpret(proposal(), text) == Answer(text=text, decision="reject")


def test_a_json_or_yaml_example_is_a_correction_with_that_example():
    corrected = {"inputs": {"invoice_text": "x"}, "exit": "done", "outputs": {"total": 1}}
    assert interpret(proposal(), json.dumps(corrected)) == Answer(text=json.dumps(corrected), decision="correct",
                                                                  example=corrected)
    text = "exit: not_an_invoice\ninputs: {invoice_text: y}\n"
    assert interpret(proposal(), text).example == {"inputs": {"invoice_text": "y"}, "exit": "not_an_invoice",
                                                   "outputs": {}}


@pytest.mark.parametrize("text", ["It should be not_an_invoice: we never pay without a due date.",
                                  '{"inputs": {}, "outputs": {}}', "exit: done\ninputs: [1, 2]\n", "{unclosed"])
def test_other_text_is_a_correction_the_pipeline_interprets(text):
    assert interpret(proposal(), text) == Answer(text=text, decision="correct")


def test_clarification_answers_are_kept_as_text():
    assert interpret(clarification(), "yes") == Answer(text="yes")


def test_question_ids_are_readable_and_prefixed_in_child_processes():
    assert question_id(root="invoices", process="invoices", node="extract", suffix="example1") == "extract.example1"
    assert question_id(root="invoices", process="team/ocr", node="read", suffix="clarify2") == "team/ocr:read.clarify2"


def test_fingerprints_depend_on_kind_text_and_proposed_example():
    base = question_fingerprint("example_proposal", "t", PROPOSED)
    assert base.startswith("sha256:")
    assert base == question_fingerprint("example_proposal", "t", dict(reversed(PROPOSED.items())))
    assert base != question_fingerprint("example_proposal", "t2", PROPOSED)
    assert base != question_fingerprint("example_proposal", "t", {**PROPOSED, "exit": "done"})
    assert base != question_fingerprint("clarification", "t", PROPOSED)


# --- events, usage, memo --------------------------------------------------------------------------------------------

def test_events_are_numbered_in_order():
    s = new_session()
    s.emit("note", "Compiling invoices.", process="invoices", step=None)
    s.emit("decision", "read: deterministic", process="invoices", step="read", data={"rule": 1})
    assert [(e.seq, e.type, e.step, e.data) for e in s.data.events] == [(1, "note", None, {}),
                                                                         (2, "decision", "read", {"rule": 1})]


def test_usage_is_totalled_by_kind_tier_and_node():
    s = new_session()
    call = Usage(input_tokens=100, output_tokens=10, cost_usd=0.5, latency_ms=1000, calls=1)
    s.record_usage(kind="decide", node="extract", tier="strong", usage=call)
    s.record_usage(kind="infer_schema", node="extract", tier="standard", usage=call)
    s.record_usage(kind="decide", node="team/ocr:read", tier="strong", usage=call)
    usage = s.data.report.usage
    assert s.data.usage == usage.total == Usage(input_tokens=300, output_tokens=30, cost_usd=1.5, latency_ms=3000,
                                                calls=3)
    assert (usage.by_kind["decide"].calls, usage.by_kind["infer_schema"].calls) == (2, 1)
    assert (usage.by_tier["strong"].calls, usage.by_tier["standard"].calls) == (2, 1)
    steps = {(e.process, e.node): e.usage.compiler.calls for e in s.data.report.steps}
    assert steps == {("invoices", "extract"): 2, ("team/ocr", "read"): 1}


def entry(step: str, size: int) -> dict:
    return {"kind": "decide", "step": step, "response": {"text": "x" * size}, "usage": {}}


def test_the_memo_is_capped_dropping_finished_steps_first_then_the_oldest():
    s = new_session()
    s.data.steps.append(CompileStep(process="invoices", step="read", use="./steps/read_pdf", package="read_pdf",
                                    phase="done"))
    memo = s.data.memo
    memo.calls.update({"old": entry("extract", 1000), "read1": entry("read", 1000), "mid": entry("fix", 1000)})
    memo.tests["read2"] = entry("read", 1000)
    memo.calls["new"] = entry("fix", 1000)
    s.trim_memo(limit=100_000)
    assert list(memo.calls) == ["old", "read1", "mid", "new"]       # under the cap: untouched
    s.trim_memo(limit=3500)
    assert (list(memo.calls), list(memo.tests)) == (["old", "mid", "new"], [])
    s.trim_memo(limit=2500)
    assert list(memo.calls) == ["mid", "new"]
    assert len(json.dumps(memo.model_dump(mode="json"))) <= 2500


def test_next_trims_the_memo(monkeypatch):
    fake_pipeline(monkeypatch, S.DONE)
    s = new_session()
    s.data.memo.calls.update({f"k{i}": entry("x", 300_000) for i in range(10)})
    s.next(None)
    assert list(s.data.memo.calls) == [f"k{i}" for i in range(4, 10)]


def test_session_data_validates_its_state():
    with pytest.raises(ValueError):
        SessionData.model_validate({**new_session().to_json(), "state": "compiling"})

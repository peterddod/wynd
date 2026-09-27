import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from support.cmp_b_doubles import FakeSession

from wynd.compiler.examples import (
    ExampleContext,
    answered_examples,
    answered_guidance,
    append_examples,
    ask_for_examples,
    contradictions,
    example_problems,
    fixture_files,
    next_question_id,
    parse_examples_answer,
    proposal_problem,
    propose_and_confirm,
)
from wynd.compiler.llm import LLMResult
from wynd.compiler.session import Answer
from wynd.runtime.usage import Usage
from wynd.spec.proto_step import Example, ProtoStep
from wynd.spec.typelang import parse_type
from wynd.spec.yamlio import parse_model


def _ctx(tmp_path: Path, **kw) -> ExampleContext:
    base = dict(
        process="mini", node="parse", package="parse_amount", prefix="", instruction="Parse an amount.",
        inputs={"text": parse_type("string"), "source": parse_type("path?")},
        outputs={"done": {"amount": parse_type("number"), "on": parse_type("date?")}, "unreadable": {}},
        base_dir=tmp_path,
    )
    return ExampleContext(**{**base, **kw})


def ex(inputs, exit="done", outputs=None, **kw) -> Example:
    return Example(inputs=inputs, outputs=outputs or {}, exit=exit, **kw)


class FakeLLM:
    def __init__(self, responses: dict[str, list[dict]]):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[tuple[str, str, str, str]] = []

    def call(self, kind, *, node, system, prompt, response_model, tier, thinking):
        self.calls.append((kind, node, prompt, str(tier)))
        return LLMResult(response_model.model_validate(self.responses[kind].pop(0)), Usage(), memo_hit=False)


def proposal(question, inputs, exit="done", outputs=None, rationale="boundary"):
    return {"question": question, "rationale": rationale, "exit": exit, "inputs_json": json.dumps(inputs),
            "outputs_json": json.dumps(outputs or {})}


# --- checks --------------------------------------------------------------------------------------------------------

def test_unknown_exit_and_conformance_problems(tmp_path):
    ctx = _ctx(tmp_path)
    problems = example_problems([
        ex({"text": "12"}, outputs={"amount": 12}),
        ex({"text": "x"}, exit="nope"),
        ex({"text": 3}, outputs={"amount": 1}),
        ex({}, outputs={"amount": 1}),
    ], ctx)
    assert any("example 2" in p and "not a declared exit" in p for p in problems)
    assert any("example 3" in p for p in problems)
    assert any("example 4" in p and "missing required input 'text'" in p for p in problems)
    assert not any("example 1" in p for p in problems)


def test_contradictions_after_canonicalisation(tmp_path):
    ctx = _ctx(tmp_path)
    examples = [
        ex({"text": "12.50"}, outputs={"amount": 12.5}),
        ex({"text": "12.50"}, exit="unreadable"),
        ex({"text": "3"}, outputs={"amount": 3, "on": "2026-01-02"}),
        ex({"text": "3"}, outputs={"amount": 3.0, "on": "2026-01-02"}),     # same after canonicalisation
        ex({"text": "4"}, outputs={"amount": 4}),
        ex({"text": "4"}, outputs={"amount": 5}),
    ]
    assert contradictions(examples, ctx) == [
        "examples 1 and 2 have the same inputs but expect different exits (done and unreadable)",
        "examples 5 and 6 have the same inputs but expect different outputs",
    ]
    assert example_problems(examples, ctx) == contradictions(examples, ctx)


def test_proposal_problems(tmp_path):
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "a.txt").write_text("12")
    ctx = _ctx(tmp_path)
    existing = [ex({"text": "12"}, outputs={"amount": 12})]
    assert proposal_problem(ex({"text": "0"}, outputs={"amount": 0}), ctx, existing) is None
    assert "same inputs as example 1" in proposal_problem(ex({"text": "12"}, exit="unreadable"), ctx, existing)
    assert "does not exist" in proposal_problem(ex({"text": "1", "source": "examples/missing.txt"},
                                                   outputs={"amount": 1}), ctx, existing)
    assert proposal_problem(ex({"text": "1", "source": "examples/a.txt"}, outputs={"amount": 1}), ctx, existing) \
        is None
    assert proposal_problem(ex({"text": "1", "source": "missing.txt"}, exit="error"), ctx, existing) is None
    assert proposal_problem(ex({"text": "1", "source": "{tmp}/a.txt"}, outputs={"amount": 1}), ctx, existing) \
        is None
    assert proposal_problem(ex({"text": "1"}, outputs={"amount": "lots"}), ctx, existing) is not None


def test_fixture_files_lists_data_files_only(tmp_path):
    (tmp_path / "examples").mkdir()
    (tmp_path / "examples" / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "steps" / "x").mkdir(parents=True)
    (tmp_path / "steps" / "x" / "data.txt").write_text("no")
    (tmp_path / "process.yaml").write_text("kind: process")
    assert fixture_files(tmp_path) == ["examples/a.pdf"]


# --- proposals ------------------------------------------------------------------------------------------------------

def test_proposals_dropped_confirmed_rejected_and_pending(tmp_path):
    ctx = _ctx(tmp_path)
    llm = FakeLLM({"propose_examples": [{"proposals": [
        {**proposal("bad json", {}), "inputs_json": "[1, 2"},
        proposal("wrong type", {"text": "1"}, outputs={"amount": "one"}),
        proposal("What about zero?", {"text": "0"}, outputs={"amount": 0}),
        proposal("What about words?", {"text": "twelve"}, exit="unreadable"),
        proposal("What about euros?", {"text": "EUR 3"}, outputs={"amount": 3}),
    ]}]})
    session = FakeSession({"parse.example1": Answer(text="accept", decision="confirm"),
                           "parse.example2": Answer(text="reject", decision="reject")})
    out = propose_and_confirm(session, ctx, llm=llm, examples=[ex({"text": "12"}, outputs={"amount": 12})],
                              rejected=["What about negative amounts?"], process_context={"goal": "g"},
                              guidance=[], max_proposals=5)
    assert out.proposed == 3
    assert len(out.warnings) == 2 and all(w.startswith("dropped an invalid proposal") for w in out.warnings)
    assert out.added == [ex({"text": "0"}, outputs={"amount": 0.0}, description="What about zero?")]
    assert out.rejected == ["What about words?"]
    assert out.pending                                      # example3 is unanswered
    asked = session.data.questions
    assert [q.id for q in asked] == ["parse.example1", "parse.example2", "parse.example3"]
    assert asked[0].kind == "example_proposal" and asked[0].default == "accept" and asked[0].expects == "decision"
    assert asked[0].proposed == {"inputs": {"text": "0"}, "outputs": {"amount": 0.0}, "exit": "done"}
    assert asked[0].detail.startswith("Why: boundary")
    kind, node, prompt, tier = llm.calls[0]
    assert (kind, node, tier) == ("propose_examples", "parse", "strong")
    assert "What about negative amounts?" in prompt


def test_max_proposals_caps_what_is_asked(tmp_path):
    ctx = _ctx(tmp_path)
    llm = FakeLLM({"propose_examples": [{"proposals": [
        proposal(f"q{i}", {"text": str(i)}, outputs={"amount": i}) for i in range(5)]}]})
    session = FakeSession()
    out = propose_and_confirm(session, ctx, llm=llm, examples=[], rejected=[], process_context={}, guidance=[],
                              max_proposals=2)
    assert out.proposed == 2 and [q.id for q in session.data.questions] == ["parse.example1", "parse.example2"]


def test_structured_correction_is_used_when_it_conforms(tmp_path):
    ctx = _ctx(tmp_path, prefix="child/p:")
    llm = FakeLLM({"propose_examples": [{"proposals": [proposal("Zero?", {"text": "0"}, outputs={"amount": 0})]}]})
    corrected = {"inputs": {"text": "0"}, "exit": "unreadable", "outputs": {}}
    session = FakeSession({"child/p:parse.example1": Answer(text=json.dumps(corrected), decision="correct",
                                                            example=corrected)})
    out = propose_and_confirm(session, ctx, llm=llm, examples=[], rejected=[], process_context={}, guidance=[],
                              max_proposals=3)
    assert out.added == [ex({"text": "0"}, exit="unreadable", description="Zero?")]
    assert out.corrected == 1 and not out.pending
    assert [c[0] for c in llm.calls] == ["propose_examples"]    # no revise_example call needed


def test_free_text_correction_goes_through_revise_example(tmp_path):
    ctx = _ctx(tmp_path)
    llm = FakeLLM({
        "propose_examples": [{"proposals": [proposal("Zero?", {"text": "0"}, outputs={"amount": 0}),
                                            proposal("Blank?", {"text": ""}, exit="unreadable")]}],
        "revise_example": [
            {"drop": False, "exit": "unreadable", "inputs_json": '{"text": "0"}', "outputs_json": "{}",
             "understood": "A zero amount means the text could not be read."},
            {"drop": True, "exit": "done", "inputs_json": "{}", "outputs_json": "{}", "understood": "not a case"},
        ],
    })
    session = FakeSession({"parse.example1": Answer(text="zero is unreadable", decision="correct"),
                           "parse.example2": Answer(text="does not matter", decision="correct")})
    out = propose_and_confirm(session, ctx, llm=llm, examples=[], rejected=[], process_context={}, guidance=[],
                              max_proposals=3)
    assert out.added == [ex({"text": "0"}, exit="unreadable", description="Zero?")]
    assert out.rejected == ["Blank?"]                       # a drop from revise_example counts as reject
    assert ("answer", "A zero amount means the text could not be read.") in session.events
    revise_prompt = llm.calls[1][2]
    assert "zero is unreadable" in revise_prompt
    assert llm.calls[1][3] == "standard"


# --- clarifications about examples -----------------------------------------------------------------------------------

def test_question_ids_count_answered_questions(tmp_path):
    ctx = _ctx(tmp_path)
    session = FakeSession({"parse.examples1": Answer(text="- inputs: {text: '1'}\n  exit: done\n  outputs: "
                                                        "{amount: 1}\n")})
    assert next_question_id(session, ctx, "examples") == "examples1"
    assert ask_for_examples(session, ctx) is not None
    assert next_question_id(session, ctx, "examples") == "examples2"
    assert session.data.questions[0].expects == "examples" and session.data.questions[0].default is None
    examples, problems = answered_examples(session, ctx)
    assert problems == [] and examples == [ex({"text": "1"}, outputs={"amount": 1.0})]


def test_answered_guidance_only_text_clarifications_of_the_node(tmp_path):
    ctx = _ctx(tmp_path)
    session = FakeSession()
    for qid, expects, text in (("parse.clarify1", "text", "treat blanks as unreadable"),
                               ("parse.examples1", "examples", "- inputs: {}"),
                               ("other.clarify1", "text", "not mine")):
        q = SimpleNamespace(id=qid, expects=expects, status="answered", answer=Answer(text=text))
        session.data.questions.append(q)
    assert answered_guidance(session, ctx) == ["treat blanks as unreadable"]


@pytest.mark.parametrize(("text", "problem"), [
    ("{not yaml", "not YAML"),
    ("just words", "YAML list of examples"),
    ("- inputs: {text: '1'}\n  exit: nope\n", "not a declared exit"),
])
def test_parse_examples_answer_problems(tmp_path, text, problem):
    examples, problems = parse_examples_answer(text, _ctx(tmp_path))
    assert examples == [] and any(problem in p for p in problems)


# --- appending to the proto -----------------------------------------------------------------------------------------

PROTO = """\
kind: proto_step
name: parse_amount
instruction: Parse an amount.   # keep me
inputs:
  text: string
outputs:
  done: { amount: number, on: "date?" }
  unreadable: {}
examples:
  # hand-written
  - inputs: { text: "£12.50" }
    outputs: { amount: 12.5 }
    exit: done
"""


def test_append_examples_keeps_comments_and_dumps_dates():
    proto = parse_model(PROTO, ProtoStep)
    added = [ex({"text": "3 Jan"}, outputs={"amount": 3.0, "on": "2026-01-03"}, description="Dated?"),
             ex({"text": ""}, exit="unreadable", description="Blank?")]
    text, reformatted = append_examples(PROTO, proto, added, session_id="job_1")
    assert not reformatted
    assert text.startswith(PROTO)
    assert text[len(PROTO):] == (
        "  # confirmed during wynd compile (session job_1)\n"
        "  - inputs: {text: 3 Jan}\n"
        "    outputs: {amount: 3.0, 'on': 2026-01-03}\n"
        "    exit: done\n"
        "    description: Dated?\n"
        "  - inputs: {text: ''}\n"
        "    exit: unreadable\n"
        "    description: Blank?\n"
    )
    new = parse_model(text, ProtoStep)
    assert [e.description for e in new.examples] == [None, "Dated?", "Blank?"]
    assert new.examples[1].outputs["on"].isoformat() == "2026-01-03"


def test_append_examples_falls_back_to_a_redump():
    text = PROTO.split("examples:")[0] + 'examples: [{inputs: {text: "1"}, outputs: {amount: 1}}]\n'
    proto = parse_model(text, ProtoStep)
    new, reformatted = append_examples(text, proto, [ex({"text": "2"}, outputs={"amount": 2.0}, description="Two?")],
                                       session_id="job_1")
    assert reformatted
    assert "# keep me" not in new
    parsed = parse_model(new, ProtoStep)
    assert [e.inputs["text"] for e in parsed.examples] == ["1", "2"]
    assert parsed.examples[1].description == "Two?"


def test_append_nothing_is_identity():
    assert append_examples(PROTO, parse_model(PROTO, ProtoStep), [], session_id="j") == (PROTO, False)

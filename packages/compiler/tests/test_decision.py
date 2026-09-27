from dataclasses import replace
from pathlib import Path

import pytest
from support.cmp_b_doubles import FakeSession, attempt_result

from wynd.compiler.codegen import CodegenContext
from wynd.compiler.decision import (
    NodeBuild,
    clarification_text,
    decide_and_build,
    score,
    split_applicable,
    split_eligible,
)
from wynd.compiler.examples import ExampleContext
from wynd.compiler.session import Answer
from wynd.compiler.testgen import step_tests
from wynd.compiler.testing import ScriptedLLM
from wynd.compiler.tools import ToolCatalog
from wynd.spec.process_doc import ProcessDoc
from wynd.spec.proto_step import Example
from wynd.spec.typelang import parse_type
from wynd.spec.yamlio import parse_model

INPUTS = {"text": parse_type("string")}
OUTPUTS = {"done": {"amount": parse_type("number")}, "unreadable": {}}
EXAMPLES = [Example(inputs={"text": t}, outputs={"amount": a}) for t, a in
            (("£12.50", 12.5), ("12.50 GBP", 12.5), ("EUR 3", 3.0), ("4", 4.0), ("twelve", 12.0), ("five", 5.0))]
ALL = {n: "handled" for n in range(1, 7)}
SPLIT = {1: "handled", 2: "handled", 3: "handled", 4: "handled", 5: "deferred", 6: "deferred"}
ONE_WRONG = {1: "handled", 2: "handled", 3: "handled", 4: "handled", 5: "deferred", 6: "wrong"}
FAILING = {1: "handled", 2: "handled", 3: "wrong", 4: "wrong", 5: "wrong", 6: "wrong"}


def code(source="class ParseAmount: ...\n", **kw):
    return {"module_source": source, "deps": [], "system_packages": [], "requires_glibc": False, "effects": [],
            "env_vars": [], "notes": "n", **kw}


def revised(verdict="fixable", diagnosis="it misread the separators", suspects=(), **kw):
    return code(diagnosis=diagnosis, verdict=verdict, suspects=[{"example": n, "why": w} for n, w in suspects], **kw)


def agentic(**kw):
    return {"docstring": "Parse the amount.", "context": [], "tools": [], "mcp": [], "tool_methods": "", "deps": [],
            "env_vars": [], "notes": "n", **kw}


def agentic_revised(verdict="fixable", diagnosis="rule unclear", suspects=()):
    return agentic(diagnosis=diagnosis, verdict=verdict, suspects=[{"example": n, "why": w} for n, w in suspects])


def decide(needs, command=False, program=""):
    return {"examples": [{"index": i, "needs": n, "why": "w"} for i, n in enumerate(needs, 1)], "command": command,
            "program": program, "summary": "Numbers in fixed formats."}


def script(*calls):
    return ScriptedLLM({"calls": [{"kind": k, "node": "parse", "response": r} for k, r in calls]})


class FakeRun:
    """Scripted attempt results per (kind, tier, package); records every run."""

    def __init__(self, results):
        self.results = {k: list(v) for k, v in results.items()}
        self.runs: list[tuple[str, str | None, str, int, str]] = []

    def __call__(self, cg, generated, n):
        key = (cg.kind, cg.tier if cg.kind == "agentic" else None, cg.package)
        self.runs.append((*key, n, cg.test_file))
        return attempt_result(self.results[key].pop(0), n=n, kind=cg.kind, tier=key[1])


def build(llm, run, tmp_path, **kw) -> NodeBuild:
    ex = ExampleContext(process="mini", node="parse", package="parse_amount", prefix="",
                        instruction="Parse an amount of money.", inputs=INPUTS, outputs=OUTPUTS, base_dir=tmp_path)
    cg = CodegenContext(kind="deterministic", node="parse", package="parse_amount", class_name="ParseAmount",
                        source="proto/parse_amount.yaml", instruction=ex.instruction, inputs=INPUTS, outputs=OUTPUTS,
                        plain=[], test_file=step_tests("ParseAmount", EXAMPLES, INPUTS,
                                                       source="proto/parse_amount.yaml"),
                        examples=list(EXAMPLES), catalog=ToolCatalog(builtins=[], mcp=[]))
    return NodeBuild(session=kw.pop("session", FakeSession()), ex=ex, cg=cg, llm=llm, run=run, **kw)


def kinds(llm):
    return [c.kind for c in llm.calls]


def test_exit_codes_mean_shell_without_a_decide_call(tmp_path):
    llm = script(("write_shell", code()))
    run = FakeRun({("shell", None, "parse_amount"): [ALL]})
    d = decide_and_build(build(llm, run, tmp_path, exit_codes={0: "done", "*": "error"}))
    assert (d.status, d.kind, d.rule) == ("built", "shell", 4)
    assert kinds(llm) == ["write_shell"]
    assert d.main.cg.exit_codes == {0: "done", "*": "error"}


def test_command_means_shell_with_the_program(tmp_path):
    llm = script(("decide", decide(["pure"] * 6, command=True, program="tr")), ("write_shell", code()))
    run = FakeRun({("shell", None, "parse_amount"): [ALL]})
    d = decide_and_build(build(llm, run, tmp_path))
    assert (d.kind, d.rule) == ("shell", 4)
    assert d.main.cg.program == "tr"
    assert "tr" in llm.calls[1].system
    assert d.classification[0] == {"example": 1, "needs": "pure", "why": "w"}


def test_failing_shell_asks_and_never_falls_back_to_an_agent(tmp_path):
    llm = script(("write_shell", code()), *[("revise_code", revised())] * 3)
    run = FakeRun({("shell", None, "parse_amount"): [FAILING] * 4})
    session = FakeSession()
    d = decide_and_build(build(llm, run, tmp_path, exit_codes={}, session=session))
    assert d.status == "awaiting"
    assert session.asked() == ["parse.clarify1"]
    assert "write_agentic" not in kinds(llm)
    assert len(run.runs) == 4


def test_at_most_half_pure_goes_straight_to_agentic(tmp_path):
    llm = script(("decide", decide(["pure", "pure", "pure", "judgement", "judgement", "world_knowledge"])),
                 ("write_agentic", agentic()))
    run = FakeRun({("agentic", "cheap", "parse_amount"): [ALL]})
    d = decide_and_build(build(llm, run, tmp_path))
    assert (d.kind, d.rule, d.tier, d.thinking) == ("agentic", 2, "cheap", "low")
    assert "write_deterministic" not in kinds(llm)
    assert d.reason.startswith("3 of 6 examples need judgement")


def test_all_pure_and_passing_is_deterministic(tmp_path):
    llm = script(("decide", decide(["pure"] * 6)), ("write_deterministic", code()))
    run = FakeRun({("deterministic", None, "parse_amount"): [ALL]})
    d = decide_and_build(build(llm, run, tmp_path))
    assert (d.status, d.kind, d.rule, d.tier) == ("built", "deterministic", 1, None)
    assert d.main.passed and d.attempts[0]["n"] == 1
    assert kinds(llm) == ["decide", "write_deterministic"]


def test_passes_after_one_revision(tmp_path):
    llm = script(("decide", decide(["pure"] * 6)), ("write_deterministic", code()),
                 ("revise_code", revised(source="class ParseAmount: pass\n")))
    run = FakeRun({("deterministic", None, "parse_amount"): [FAILING, ALL]})
    session = FakeSession()
    d = decide_and_build(build(llm, run, tmp_path, session=session))
    assert (d.kind, d.rule) == ("deterministic", 1)
    assert d.main.best.source == "class ParseAmount: pass\n"
    assert [a["n"] for a in d.attempts] == [1, 2]
    assert d.attempts[0]["diagnosis"] == "it misread the separators"
    assert ("test", "attempt 1: 2/6 passed; it misread the separators") in session.events


def test_split_when_most_handled_and_the_rest_deferred(tmp_path):
    llm = script(("decide", decide(["pure"] * 4 + ["judgement"] * 2)), ("write_deterministic", code()),
                 ("revise_code", revised(verdict="needs_judgement", diagnosis="words need judgement")),
                 ("write_agentic", agentic()))
    run = FakeRun({("deterministic", None, "parse_amount"): [SPLIT, ALL],     # the hand-off re-run passes
                   ("agentic", "cheap", "parse_amount_agentic"): [ALL]})
    session = FakeSession()
    d = decide_and_build(build(llm, run, tmp_path, session=session))
    assert (d.status, d.kind, d.rule, d.tier) == ("built", "split", 3, "cheap")
    assert (d.handled, d.deferred) == ([1, 2, 3, 4], [5, 6])
    assert d.agentic.cg.package == "parse_amount_agentic" and d.agentic.cg.class_name == "ParseAmountAgentic"
    assert d.agentic.cg.deferred == [5, 6]
    assert "ParseAmountAgentic = load_step" in d.agentic.cg.test_file
    # the deterministic half is re-run with its hand-off tests (deferred examples expect exit error)
    det_runs = [r for r in run.runs if r[0] == "deterministic"]
    assert len(det_runs) == 2
    assert 'exit="error"' in det_runs[-1][4] and 'exit="error"' not in det_runs[0][4]
    assert d.main.cg.test_file == det_runs[-1][4]
    assert "revise_code" in kinds(llm) and kinds(llm).count("revise_code") == 1    # needs_judgement stopped early
    assert any(t == "split" for t, _ in session.events)
    assert "parse.error" in d.reason


def test_one_wrong_answer_means_agentic(tmp_path):
    llm = script(("decide", decide(["pure"] * 6)), ("write_deterministic", code()),
                 *[("revise_code", revised())] * 3, ("write_agentic", agentic()))
    run = FakeRun({("deterministic", None, "parse_amount"): [ONE_WRONG] * 4,
                   ("agentic", "cheap", "parse_amount"): [ALL]})
    d = decide_and_build(build(llm, run, tmp_path))
    assert (d.kind, d.rule) == ("agentic", 2)
    assert d.reason == "deterministic code handled only 4 of 6 examples"
    assert len([r for r in run.runs if r[0] == "deterministic"]) == 4


def test_split_eligible_but_not_applicable_is_agentic_with_the_reason(tmp_path):
    llm = script(("decide", decide(["pure"] * 6)), ("write_deterministic", code()),
                 ("revise_code", revised(verdict="needs_judgement")), ("write_agentic", agentic()))
    run = FakeRun({("deterministic", None, "parse_amount"): [SPLIT, SPLIT],
                   ("agentic", "cheap", "parse_amount"): [ALL]})
    blocker = "the step comes from a step root and is shared with other processes"
    session = FakeSession()
    d = decide_and_build(build(llm, run, tmp_path, split_blocker=blocker, session=session))
    assert (d.kind, d.rule) == ("agentic", 2)
    assert blocker in d.reason and "handled 4 of 6" in d.reason
    assert any(t == "decision" and "a split is not possible here" in text for t, text in session.events)


def test_agentic_escalates_to_standard(tmp_path):
    llm = script(("decide", decide(["judgement"] * 6)), ("write_agentic", agentic()),
                 *[("revise_agentic", agentic_revised())] * 3, ("write_agentic", agentic()),
                 ("revise_agentic", agentic_revised()))
    run = FakeRun({("agentic", "cheap", "parse_amount"): [FAILING] * 4,
                   ("agentic", "standard", "parse_amount"): [FAILING, ALL]})
    d = decide_and_build(build(llm, run, tmp_path))
    assert (d.kind, d.rule, d.tier) == ("agentic", 2, "standard")
    assert "cheap failed examples 3, 4, 5, 6 after 4 attempts; standard passed" in d.reason
    assert [c.tier for c in llm.calls if c.kind == "write_agentic"] == ["strong", "strong"]
    assert [(r[1], r[3]) for r in run.runs] == [("cheap", 1), ("cheap", 2), ("cheap", 3), ("cheap", 4),
                                                ("standard", 5), ("standard", 6)]


def test_needs_stronger_model_escalates_at_once(tmp_path):
    llm = script(("decide", decide(["judgement"] * 6)), ("write_agentic", agentic()),
                 ("revise_agentic", agentic_revised(verdict="needs_stronger_model")), ("write_agentic", agentic()))
    run = FakeRun({("agentic", "cheap", "parse_amount"): [FAILING], ("agentic", "standard", "parse_amount"): [ALL]})
    d = decide_and_build(build(llm, run, tmp_path))
    assert d.tier == "standard" and len(run.runs) == 2


def test_both_tiers_fail_asks_a_clarification(tmp_path):
    llm = script(("decide", decide(["judgement"] * 6)), ("write_agentic", agentic()),
                 *[("revise_agentic", agentic_revised(diagnosis=f"cheap {i}")) for i in range(3)],
                 ("write_agentic", agentic()), ("revise_agentic", agentic_revised(diagnosis="still unclear")))
    run = FakeRun({("agentic", "cheap", "parse_amount"): [FAILING] * 4,
                   ("agentic", "standard", "parse_amount"): [FAILING] * 2})
    session = FakeSession()
    d = decide_and_build(build(llm, run, tmp_path, session=session))
    assert d.status == "awaiting"
    q = session.data.questions[0]
    assert (q.id, q.kind, q.expects, q.default) == ("parse.clarify1", "clarification", "text", None)
    assert q.text == (
        "I could not make parse_amount pass its examples after 6 attempts\n"
        "(agentic at cheap and standard tiers: 2/6 at best).\n"
        "What went wrong: still unclear\n"
        'Answer with guidance (for example "treat credit notes as not_an_invoice"); it will be used when this step\n'
        "is compiled again. To change an example itself, edit the proto-step and compile again."
    )


def test_examples_inconsistent_stops_the_loop_and_names_suspects(tmp_path):
    llm = script(("decide", decide(["pure"] * 6)), ("write_deterministic", code()),
                 ("revise_code", revised(verdict="examples_inconsistent", diagnosis="example 4 contradicts 1",
                                         suspects=[(4, "the text says 4 but 1 says otherwise")])))
    run = FakeRun({("deterministic", None, "parse_amount"): [ONE_WRONG]})
    session = FakeSession()
    d = decide_and_build(build(llm, run, tmp_path, session=session))
    assert d.status == "awaiting" and len(run.runs) == 1
    text = session.data.questions[0].text
    assert "(deterministic: 4/6 at best)." in text
    assert "These examples look inconsistent:\n  - Example 4: the text says 4 but 1 says otherwise" in text
    assert "write_agentic" not in kinds(llm)                   # inconsistent examples ask, never fall back


def test_answered_clarification_recompiles_with_the_guidance(tmp_path):
    llm = script(("decide", decide(["pure"] * 6)), ("write_deterministic", code()),
                 ("revise_code", revised(verdict="examples_inconsistent")),
                 ("decide", decide(["pure"] * 6)), ("write_deterministic", code()))
    run = FakeRun({("deterministic", None, "parse_amount"): [ONE_WRONG, ALL]})
    session = FakeSession({"parse.clarify1": Answer(text="amounts in words are pounds")})
    d = decide_and_build(build(llm, run, tmp_path, session=session))
    assert (d.status, d.kind) == ("built", "deterministic")
    assert "amounts in words are pounds" in llm.prompts("decide")[1]
    assert [a["n"] for a in d.attempts] == [1, 2]


def test_split_eligibility():
    assert split_eligible(attempt_result(SPLIT))
    assert not split_eligible(attempt_result(ONE_WRONG))
    assert not split_eligible(attempt_result(ALL))                                   # nothing deferred
    assert not split_eligible(attempt_result({1: "handled", 2: "handled", 3: "deferred", 4: "deferred"}))  # half


def test_attempt_scoring():
    det = [attempt_result(ONE_WRONG), attempt_result(SPLIT), attempt_result({**SPLIT, 4: "deferred"})]
    best = max(range(3), key=lambda i: score("deterministic", det[i], i))
    assert best == 1                                             # zero wrong beats more handled with a wrong one
    agt = [attempt_result(FAILING), attempt_result(ONE_WRONG), attempt_result(ONE_WRONG)]
    assert max(range(3), key=lambda i: score("agentic", agt[i], i)) == 1          # earliest of the best


def test_clarification_text_counts_loops():
    class Loop:
        def __init__(self, kind, tier, handled, n, diagnoses=(), suspects=()):
            self.kind, self.tier, self.diagnoses, self.suspects = kind, tier, list(diagnoses), list(suspects)
            self.cg = replace(CodegenContext(kind=kind, node="n", package="p", class_name="P", source="s",
                                             instruction="i", inputs={}, outputs={}, plain=[], test_file="",
                                             examples=list(EXAMPLES), catalog=ToolCatalog([], [])))
            self.best_result = attempt_result({i: "handled" if i <= handled else "wrong" for i in range(1, 7)})
            self.results = [self.best_result] * n

    text = clarification_text("extract_invoice_fields", [Loop("deterministic", None, 3, 4),
                                                         Loop("agentic", "cheap", 5, 4, ["cheap diag"]),
                                                         Loop("agentic", "standard", 4, 2)])
    assert text.splitlines()[:3] == [
        "I could not make extract_invoice_fields pass its examples after 10 attempts",
        "(deterministic: 3/6 at best; agentic at cheap and standard tiers: 5/6 at best).",
        "What went wrong: cheap diag",
    ]


# --- split applicability ---------------------------------------------------------------------------------------------

DOGFOOD = Path(__file__).resolve().parents[3] / "examples" / "invoices" / "processes" / "process_supplier_invoice"


def _dogfood() -> ProcessDoc:
    return parse_model((DOGFOOD / "process.yaml").read_text(), ProcessDoc)


def test_dogfood_extract_is_splittable():
    assert split_applicable(_dogfood(), "extract", ref_kind="local", existing_packages={"read_pdf"}) is None


@pytest.mark.parametrize(("node", "kw", "reason"), [
    ("extract", {"ref_kind": "root"}, "the step comes from a step root and is shared with other processes"),
    ("extract", {"ref_kind": "local", "existing_packages": {"extract_invoice_fields_agentic"}},
     "name `extract_agentic` is already taken"),
    ("read", {"ref_kind": "local"},
     "edge `validate.done` references `steps.read.outputs.text`, which would be missing when the agentic half ran"),
    ("validate", {"ref_kind": "local"}, None),       # only its own edges read steps.validate
])
def test_split_applicability(node, kw, reason):
    assert split_applicable(_dogfood(), node, **kw) == reason


def test_split_blocked_by_an_error_edge_or_shared_package():
    text = (DOGFOOD / "process.yaml").read_text()
    with_error = text + "  - from: extract.error\n    to: escalate\n    with: { fields: steps.read.outputs, " \
                        "errors: '[]', queue_dir: env.ESCALATIONS_DIR, run_id: run.id }\n"
    doc = parse_model(with_error, ProcessDoc)
    assert split_applicable(doc, "extract", ref_kind="local") == "an edge already handles `extract.error`"
    shared = text.replace("  escalate: { use: ./steps/escalate_to_human }\n",
                          "  escalate: { use: ./steps/escalate_to_human }\n"
                          "  extract2: { use: ./steps/extract_invoice_fields }\n")
    doc = parse_model(shared, ProcessDoc)
    assert split_applicable(doc, "extract", ref_kind="local") == "the package is used by several nodes"

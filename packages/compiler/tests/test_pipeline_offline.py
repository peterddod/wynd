"""Offline end-to-end compile of `fixtures/ws_mini` ($DRAFTS/05 §16.2, PLAN §7.2): the real pipeline, attempts,
venvs, step suites and process examples, with a `ScriptedLLM` for the compiler's calls and the runtime `fake`
provider for the agentic steps.

Scenarios: straight through; questions and resume as the same job requeued; clarification; skip-and-preserve
(incl. split removal); the user's working tree untouched; integration failure.
"""

import dataclasses
import json
from pathlib import Path

import pytest
import yaml

from wynd.compiler import jobs
from wynd.compiler.llm import MemoLLM
from wynd.compiler.testing import FakeJobContext, ScriptedLLM
from wynd.process.git import rev_parse
from wynd.spec.lockfiles import StepLock
from wynd.spec.yamlio import parse_model

FIXTURES = Path(__file__).parent / "fixtures"
CODE = FIXTURES / "code"
STEPS = "processes/mini/steps"


def code(name: str, **kw) -> dict:
    return {"module_source_file": str(CODE / name), "deps": [], "system_packages": [], "requires_glibc": False,
            "effects": [], "env_vars": [], "notes": "n", **kw}


def revised(name: str, verdict: str = "fixable", diagnosis: str = "d", suspects=()) -> dict:
    return {**code(name), "diagnosis": diagnosis, "verdict": verdict,
            "suspects": [{"example": n, "why": w} for n, w in suspects]}


def agentic(docstring: str) -> dict:
    return {"docstring": docstring, "context": ["process.goal"], "tools": [], "mcp": [], "tool_methods": "",
            "deps": [], "env_vars": [], "notes": "n"}


def decide(needs: list[str]) -> dict:
    return {"examples": [{"index": i, "needs": n, "why": "w"} for i, n in enumerate(needs, 1)], "command": False,
            "program": "", "summary": "Classified."}


NO_PROPOSALS = {"proposals": []}
THREE_PARTS = {"proposals": [{"question": "What if the name has three parts?", "rationale": "r", "exit": "done",
                              "inputs_json": json.dumps({"name": "mary ann evans"}),
                              "outputs_json": json.dumps({"name": "Mary Ann Evans"})}]}


def mini_calls(**replace) -> list[tuple[str, str, dict]]:
    """(node, kind, response) for a straight-through compile of every node; `replace[node]` swaps a node's calls."""
    nodes = {
        "normalise": [("propose_examples", THREE_PARTS), ("decide", decide(["pure"] * 3)),
                      ("write_deterministic", code("normalise_name_v1.py")),
                      ("revise_code", revised("normalise_name_v2.py", diagnosis="it did not title-case"))],
        "classify": [("propose_examples", NO_PROPOSALS), ("decide", decide(["judgement"] * 2)),
                     ("write_agentic", agentic("Classify the support message."))],
        "parse": [("propose_examples", NO_PROPOSALS), ("decide", decide(["pure"] * 3 + ["judgement"])),
                  ("write_deterministic", code("parse_amount_v1.py")),
                  ("revise_code", revised("parse_amount_v1.py", verdict="needs_judgement")),
                  ("write_agentic", agentic("Read the amount of money in the text."))],
        "shout": [("propose_examples", NO_PROPOSALS), ("write_shell", code("shout.py", effects=["shell"]))],
        "store": [("propose_examples", NO_PROPOSALS), ("decide", decide(["pure"])),
                  ("write_deterministic", code("store_note.py", effects=["filesystem"]))],
    }
    nodes.update(replace)
    return [(node, kind, response) for node, calls in nodes.items() for kind, response in calls]


def scripted(calls) -> ScriptedLLM:
    return ScriptedLLM({"calls": [{"kind": kind, "node": node, "response": response} for node, kind, response in calls]})


@pytest.fixture
def ws(make_repo, monkeypatch) -> Path:
    root = make_repo(FIXTURES / "ws_mini")
    monkeypatch.setenv("WYND_FAKE_PROVIDER_SCRIPT", str(root / "fake_provider.json"))
    return root


@pytest.fixture
def compile_job(monkeypatch):
    """Run one compile job attempt with `llm` behind the real memo and metering; returns (ctx, outcome, record)."""
    real = jobs.default_deps

    def use(llm):
        def deps(ctx, session):
            base = real(ctx, session)
            metered = jobs.MeteredLLM(llm, session)
            return dataclasses.replace(base, llm=MemoLLM(metered, session.data.memo, on_usage=lambda *a: None))

        monkeypatch.setattr(jobs, "default_deps", deps)

    def run(ws: Path, llm, *, ctx: FakeJobContext | None = None, answers=None, **inputs):
        use(llm)
        ctx = ctx.requeue(answers) if ctx is not None else FakeJobContext(ws, "mini", inputs=inputs)
        outcome = jobs.run_compile_job(ctx)
        return ctx, outcome, ctx.finish(outcome)

    return run


def lock(root: Path, package: str) -> StepLock:
    return parse_model((root / STEPS / package / "step.lock.yaml").read_text(), StepLock)


def files_at(git, ws: Path, sha: str) -> list[str]:
    return git(ws, "ls-tree", "-r", "--name-only", sha).split()


def fast_forward(git, ws: Path, sha: str) -> None:
    git(ws, "merge", "-q", "--ff-only", sha)


def actions(outcome) -> dict[str, str]:
    return {s["node"]: s["action"] for s in outcome.report["steps"]}


def test_straight_through(ws, compile_job, git, tree_state):
    base = rev_parse(ws, "HEAD")
    before = tree_state(ws)
    llm = scripted(mini_calls())
    _, outcome, record = compile_job(ws, llm, accept_proposals=True)

    assert outcome.status == "succeeded", outcome.error
    assert tree_state(ws) == before
    assert git(ws, "rev-list", f"{base}..{outcome.commit}").split() == [outcome.commit]      # one squash on base
    assert record.result_branch == f"wynd/compile/mini/{record.id}"
    assert len(llm.calls) == len(mini_calls())

    fast_forward(git, ws, outcome.commit)
    kinds = {p: lock(ws, p).kind for p in ("normalise_name", "classify_ticket", "parse_amount",
                                           "parse_amount_agentic", "shout", "store_note")}
    assert kinds == {"normalise_name": "deterministic", "classify_ticket": "agentic", "parse_amount": "deterministic",
                     "parse_amount_agentic": "agentic", "shout": "shell", "store_note": "deterministic"}
    assert lock(ws, "parse_amount").compiled["split"]["deferred"] == [4]
    assert lock(ws, "parse_amount_agentic").compiled["split"]["role"] == "agentic"
    cassettes = sorted({Path(f).parts[3] if f.startswith(STEPS) else Path(f).parts[2]
                        for f in files_at(git, ws, outcome.commit) if "/cassettes/" in f})
    assert cassettes == ["cassettes", "classify_ticket", "parse_amount_agentic"]      # process-level + agentic steps
    process = (ws / "processes/mini/process.yaml").read_text()
    assert "# wynd:split parse begin" in process and "parse_agentic: {use: ./steps/parse_amount_agentic}" in process
    proto = (ws / "processes/mini/proto/normalise_name.yaml").read_text()
    assert "description: What if the name has three parts?" in proto
    for package in ("normalise_name", "parse_amount", "shout", "store_note"):
        assert (ws / STEPS / package / f"{package}.py").read_text() == \
            (CODE / {"normalise_name": "normalise_name_v2.py", "parse_amount": "parse_amount_v1.py"}
             .get(package, f"{package}.py")).read_text()

    report = outcome.report
    assert actions(outcome) == dict.fromkeys(["normalise", "classify", "parse", "shout", "store"], "compiled")
    assert report["integration_tests"] == {"passed": 2, "failed": 0, "cases": []}
    assert report["process_changes"][0]["added_node"] == "parse_agentic"
    assert report["proto_changes"] == [{"proto": "processes/mini/proto/normalise_name.yaml", "examples_added": 1}]
    assert [s["decision"]["rule"] for s in report["steps"]] == [1, 2, 3, 4, 1]
    assert outcome.artefacts["split"] == ["parse"]


def test_questions_and_resume_as_the_same_job(ws, compile_job, git, tree_state):
    base = rev_parse(ws, "HEAD")
    before = tree_state(ws)
    llm = scripted(mini_calls())
    ctx, outcome, record = compile_job(ws, llm)
    assert outcome.status == "awaiting_input"
    assert [q["id"] for q in outcome.questions] == ["normalise.example1"]
    wip = files_at(git, ws, record.result_commit)
    assert f"{STEPS}/classify_ticket/step.lock.yaml" in wip
    assert not any(f.startswith(f"{STEPS}/normalise_name/") for f in wip)
    assert tree_state(ws) == before

    _, outcome, record = compile_job(ws, llm, ctx=ctx, answers={"normalise.example1": "accept"})
    assert outcome.status == "succeeded", outcome.error
    assert actions(outcome)["normalise"] == "compiled"
    assert {actions(outcome)[n] for n in ("classify", "parse", "shout", "store")} == {"skipped"}
    assert git(ws, "rev-list", f"{base}..{outcome.commit}").split() == [outcome.commit]
    assert [c.n for c in llm.calls if (c.kind, c.node) == ("propose_examples", "normalise")] == [1]   # memo hit
    assert len(llm.calls) == len(mini_calls())                   # memoised work is never asked again
    assert tree_state(ws) == before


def test_clarification_answer_becomes_guidance(ws, compile_job, git):
    normalise = [("propose_examples", NO_PROPOSALS), ("decide", decide(["pure"] * 2)),
                 ("write_deterministic", code("normalise_name_v1.py")),
                 ("revise_code", revised("normalise_name_v1.py", verdict="examples_inconsistent",
                                         diagnosis="example 1 is not title case", suspects=[(1, "odd casing")])),
                 ("propose_examples", NO_PROPOSALS), ("decide", decide(["pure"] * 2)),
                 ("write_deterministic", code("normalise_name_v2.py"))]
    llm = scripted(mini_calls(normalise=normalise))
    ctx, outcome, _ = compile_job(ws, llm, accept_proposals=True)
    assert outcome.status == "awaiting_input"
    (question,) = outcome.questions
    assert question["id"] == "normalise.clarify1" and question["kind"] == "clarification"
    assert "Example 1: odd casing" in question["text"]

    _, outcome, _ = compile_job(ws, llm, ctx=ctx, answers={"normalise.clarify1": "Title-case every word."})
    assert outcome.status == "succeeded", outcome.error
    assert "Title-case every word." in llm.prompts("decide", "normalise")[1]
    fast_forward(git, ws, outcome.commit)
    assert lock(ws, "normalise_name").compiled["guidance"] == ["Title-case every word."]


def test_skip_and_preserve(ws, compile_job, git, commit):
    _, outcome, _ = compile_job(ws, scripted(mini_calls()), accept_proposals=True)
    fast_forward(git, ws, outcome.commit)

    module = ws / STEPS / "normalise_name" / "normalise_name.py"
    edited = module.read_text() + "# reviewed by a human\n"
    commit(ws, "hand edit", {f"{STEPS}/normalise_name/normalise_name.py": edited})
    _, outcome, record = compile_job(ws, scripted([]))
    assert outcome.status == "succeeded" and outcome.commit is None and record.result_branch is None
    assert set(actions(outcome).values()) == {"skipped"}
    assert module.read_text() == edited

    proto = ws / "processes/mini/proto/classify_ticket.yaml"
    commit(ws, "reword", {"processes/mini/proto/classify_ticket.yaml":
                          proto.read_text().replace("Decide whether", "Decide carefully whether")})
    llm = scripted([("classify", "propose_examples", NO_PROPOSALS),
                    ("classify", "decide", decide(["judgement"] * 2)),
                    ("classify", "write_agentic", agentic("Classify the support message, carefully."))])
    _, outcome, _ = compile_job(ws, llm)
    assert outcome.status == "succeeded", outcome.error
    assert {n for n, a in actions(outcome).items() if a == "compiled"} == {"classify"}
    assert "Classify the support message." in llm.prompts("write_agentic", "classify")[0]
    fast_forward(git, ws, outcome.commit)
    assert module.read_text() == edited

    proto = ws / "processes/mini/proto/parse_amount.yaml"
    commit(ws, "reword", {"processes/mini/proto/parse_amount.yaml":
                          proto.read_text().replace("Read the amount", "Read the amount (digits or words)")})
    llm = scripted([("parse", "propose_examples", NO_PROPOSALS), ("parse", "decide", decide(["pure"] * 4)),
                    ("parse", "write_deterministic", code("parse_amount_all.py"))])
    _, outcome, _ = compile_job(ws, llm)
    assert outcome.status == "succeeded", outcome.error
    fast_forward(git, ws, outcome.commit)
    process = (ws / "processes/mini/process.yaml").read_text()
    assert "wynd:split" not in process and "parse_agentic" not in process
    assert not (ws / STEPS / "parse_amount_agentic").exists()
    assert lock(ws, "parse_amount").compiled["split"] is None
    assert outcome.report["process_changes"][0]["type"] == "unsplit"


PLAIN_EDGE = "  - from: classify.done\n    to: parse\n    with: { text: '\"£12.50\"' }\n"
AGENTIC_EDGE = ("  - from: classify.done\n    kind: agentic\n    to:\n      - step: parse\n        name: real\n"
                "        check: The message is a genuine support request.\n        with: { text: '\"£12.50\"' }\n"
                "      - $exit.spam\n")
GENUINE = {"match": {"input": {"transition": {"from": "classify.done", "to": "parse"}}},
           "output": {"take": True, "reason": "Ada Lovelace is a billing request."}}


def test_a_new_agentic_branch_is_locked_and_recorded_with_every_step_skipped(ws, compile_job, git, commit):
    """PLAN §7 item 7 (M5): a changed `edges.lock.yaml` re-records the process examples, so the result commit
    replays the new check although no step compiles."""
    _, outcome, _ = compile_job(ws, scripted(mini_calls()), accept_proposals=True)
    fast_forward(git, ws, outcome.commit)
    process = (ws / "processes/mini/process.yaml").read_text()
    assert PLAIN_EDGE in process
    script = json.loads((ws / "fake_provider.json").read_text())
    commit(ws, "agentic classify edge", {"processes/mini/process.yaml": process.replace(PLAIN_EDGE, AGENTIC_EDGE),
                                         "fake_provider.json": json.dumps({"responses": [
                                             GENUINE, *script["responses"]]})})

    _, outcome, record = compile_job(ws, scripted([]))
    assert outcome.status == "succeeded", outcome.error
    assert set(actions(outcome).values()) == {"skipped"}
    assert outcome.report["process_changes"] == [{"type": "edges_lock", "process": "mini",
                                                  "branches": ["classify.done[real]"]}]
    assert outcome.report["integration_tests"]["passed"] == 2
    files = files_at(git, ws, outcome.commit)
    assert "processes/mini/edges.lock.yaml" in files
    assert [f for f in files if f.startswith("processes/mini/cassettes/edges/")]
    changed = git(ws, "diff", "--name-only", "HEAD", outcome.commit).split()
    assert {f.split("/")[2] for f in changed} == {"edges.lock.yaml", "cassettes"}
    message = git(ws, "log", "-1", "--format=%B", outcome.commit)
    assert "edges.lock.yaml of mini locks classify.done[real]" in message


def test_integration_failure_publishes_the_branch(make_repo, monkeypatch, compile_job, git):
    text = (FIXTURES / "ws_mini/processes/mini/process.yaml").read_text()
    wrong = text.replace("  - inputs: { name: win a prize now }\n    env: { NOTES_DIR: \"{tmp}/notes\" }\n"
                         "    exit: spam", "  - inputs: { name: win a prize now }\n"
                                           "    env: { NOTES_DIR: \"{tmp}/notes\" }\n    exit: done")
    assert wrong != text
    ws = make_repo(FIXTURES / "ws_mini", files={"processes/mini/process.yaml": wrong})
    monkeypatch.setenv("WYND_FAKE_PROVIDER_SCRIPT", str(ws / "fake_provider.json"))
    _, outcome, record = compile_job(ws, scripted(mini_calls()), accept_proposals=True)
    assert outcome.status == "failed"
    assert outcome.commit is not None and record.result_branch == f"wynd/compile/mini/{record.id}"
    cases = outcome.report["integration_tests"]["cases"]
    assert [c["name"] for c in cases] == ["example_2"]
    assert yaml.safe_load(git(ws, "show", f"{outcome.commit}:processes/mini/steps/shout/step.lock.yaml"))["kind"] \
        == "shell"

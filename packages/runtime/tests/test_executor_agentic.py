"""The executor on agentic edges (PLAN §3.5 routing, §5.4, §5.6, §15 item 28; `$DRAFTS/08 §4.2, §4.8`).

Routing rules with a fake `EdgeChecker` (a false `when` costs no check, the first positive verdict wins, negative
verdicts fall through to the else, no else -> `no_branch_matched`, checker failures -> `edge_check` for the handler,
counters, an exhausted branch costs no check), then whole runs through the real `WorkerEdgeChecker` and a
`sys.executable` venv worker running the `fake` provider: verdicts, events and usage, a child process that declares
another `provider:` (its branch still uses `RunPlan.provider`), and edge cassettes under
`<process dir>/cassettes/edges/` recorded then replayed without the provider."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest

from wynd.runtime.cassettes import promote
from wynd.runtime.edges import EdgeCheckCall, EdgeCheckError, EdgeCheckResult, EdgeVerdict, WorkerEdgeChecker
from wynd.runtime.usage import Usage
from wynd.runtime.worker.pool import WorkerPool
from wynd.spec.lockfiles import branch_key
from wynd.spec.plan import PlanVenv

SCRIPT_VAR = "WYND_FAKE_PROVIDER_SCRIPT"

DOC = """
kind: process
name: p
goal: Pay only real invoices.
entry: validate
inputs: {text: string}
outputs:
  done: {amount: integer}
  credit: {amount: integer}
  review: {save_taken: integer, credit_taken: integer, else_taken: integer}
  failed: {cause: string, error_cause: string}
steps:
  validate: {use: ./steps/validate}
  save: {use: ./steps/save}
  credit: {use: ./steps/credit}
  escalate: {use: ./steps/escalate}
edges:
  - from: validate.done
    kind: agentic
    to:
      - step: save
        name: save
        when: steps.validate.outputs.valid
        check: The document is a final invoice that requests payment.
        with: {amount: steps.validate.outputs.amount}
      - step: credit
        name: credit
        check: The document is a credit note.
        context: [steps.validate.outputs]
        with: {amount: steps.validate.outputs.amount}
      - step: escalate
  - {from: save.done, to: $exit.done, with: {amount: steps.save.outputs.amount}}
  - {from: credit.done, to: $exit.credit, with: {amount: steps.credit.outputs.amount}}
  - from: escalate.done
    to: $exit.review
    with:
      save_taken: 'edges["validate.done"].save.taken'
      credit_taken: 'edges["validate.done"].credit.taken'
      else_taken: 'edges["validate.done"][2].taken'
"""

LOOP = """
kind: process
name: p
entry: validate
inputs: {text: string}
outputs: {fixes: integer, checks: integer}
steps: {validate: {use: ./steps/validate}, fix: {use: ./steps/fix}}
edges:
  - from: validate.done
    kind: agentic
    to:
      - step: fix
        name: fix
        check: The record still has a problem a fix can repair.
        limits: {max_traversals: 2}
      - step: $exit.done
        with: {fixes: 'edges["validate.done"].fix.taken', checks: steps.validate.runs}
  - {from: fix.done, to: validate, with: {text: '"fixed"'}}
"""

SAVE, CREDIT = "validate.done[save]", "validate.done[credit]"


@dataclass
class FakeChecker:
    """`EdgeChecker` double: per branch key, a queue of verdicts (bool) or `EdgeCheckError`s."""

    answers: dict[str, list[bool | EdgeCheckError]]
    calls: list[EdgeCheckCall] = field(default_factory=list)

    def check(self, call, *, cassette, workspace, timeout_s, on_event) -> EdgeCheckResult:
        self.calls.append(call)
        answer = self.answers[call.branch_key].pop(0)
        if isinstance(answer, EdgeCheckError):
            raise answer
        return EdgeCheckResult(
            verdict=EdgeVerdict(take=answer, reason=f"{call.branch_key} -> {answer}"), attempts=1,
            validation_failures=0, provider=call.provider, tier=call.lock.tier, model_id=call.model_id,
            usage=Usage(input_tokens=10, calls=1), replayed=False, duration_ms=3.0,
        )

    def keys(self) -> list[str]:
        return [call.branch_key for call in self.calls]


@pytest.fixture
def invoice(graph, make_step):
    """`invoice(valid, checker, doc=DOC, **graph_kwargs)` -> Graph of DOC with deterministic in-process steps."""

    def make(valid: bool, checker: Any = None, doc: str = DOC, **kwargs: Any):
        validate = make_step(lambda i: {"valid": valid, "amount": 120}, inputs={"text": str},
                             done={"valid": bool, "amount": int})
        keep = make_step(lambda i: {"amount": i.amount}, inputs={"amount": int}, done={"amount": int})
        return graph(doc, {"validate": validate, "save": keep, "credit": keep, "escalate": make_step()},
                     edge_checker=checker, **kwargs)

    return make


def taken(g, run_id: str) -> list[tuple[str, int, str, list[bool]]]:
    return [(e["from"], e["branch"], e["to"], [v["take"] for v in e.get("verdicts", [])])
            for e in g.events(run_id, "edge.taken")]


# --- routing (fake checker) ----------------------------------------------------------------------------------------

def test_a_false_when_makes_no_check_call(invoice):
    checker = FakeChecker({CREDIT: [True]})
    g = invoice(valid=False, checker=checker)

    result = g.run({"text": "t"})

    assert (result.exit, result.outputs) == ("credit", {"amount": 120})
    assert checker.keys() == [CREDIT]
    assert taken(g, result.run_id)[0] == ("validate.done", 1, "credit", [True])


def test_the_first_positive_verdict_wins(invoice):
    checker = FakeChecker({SAVE: [True], CREDIT: [True]})
    g = invoice(valid=True, checker=checker)

    result = g.run({"text": "t"})

    assert (result.exit, result.outputs) == ("done", {"amount": 120})
    assert checker.keys() == [SAVE]
    [event] = g.events(result.run_id, "edge.check")
    assert (event["branch_key"], event["target"], event["take"], event["reason"]) == (SAVE, "save", True,
                                                                                      f"{SAVE} -> True")


def test_a_negative_verdict_falls_through_to_the_next_branch(invoice):
    checker = FakeChecker({SAVE: [False], CREDIT: [True]})
    g = invoice(valid=True, checker=checker)

    result = g.run({"text": "t"})

    assert result.exit == "credit"
    assert checker.keys() == [SAVE, CREDIT]
    assert [(e["branch_key"], e["take"]) for e in g.events(result.run_id, "edge.check")] == [(SAVE, False),
                                                                                           (CREDIT, True)]
    assert taken(g, result.run_id)[0] == ("validate.done", 1, "credit", [False, True])
    assert result.usage == Usage(input_tokens=20, calls=2)


def test_negative_verdicts_reach_the_else_and_only_the_taken_branch_counts(invoice):
    g = invoice(valid=True, checker=FakeChecker({SAVE: [False], CREDIT: [False]}))

    result = g.run({"text": "t"})

    assert (result.exit, result.outputs) == ("review", {"save_taken": 0, "credit_taken": 0, "else_taken": 1})
    assert taken(g, result.run_id)[0] == ("validate.done", 2, "escalate", [False, False])


def test_no_else_and_a_negative_verdict_is_no_branch_matched(invoice):
    doc = DOC.replace("      - step: escalate\n", "")
    g = invoice(valid=True, checker=FakeChecker({SAVE: [False], CREDIT: [False]}), doc=doc)

    result = g.run({"text": "t"})

    assert (result.exit, result.error.cause, result.error.edge, result.error.step) == (
        "error", "no_branch_matched", "validate.done", "validate")
    assert g.events(result.run_id, "edge.taken") == []
    assert len(g.events(result.run_id, "edge.check")) == 2


def test_a_checker_failure_is_an_edge_check_error_for_the_handler(invoice, make_step):
    escalate = "  escalate: {use: ./steps/escalate}\n"
    doc = DOC.replace("edges:\n", "on_error: handle\nedges:\n").replace(
        escalate, escalate + "  handle: {use: ./steps/handle}\n")
    handle = make_step(lambda i: {"exit": "failed", "cause": i.cause, "error_cause": i.detail["error_cause"]},
                       inputs={"cause": str, "detail": dict}, done={}, failed={"cause": str, "error_cause": str})
    checker = FakeChecker({SAVE: [EdgeCheckError("validation", "no valid structured output after 3 attempt(s)")]})
    g = invoice(valid=True, checker=checker, doc=doc)
    g.dispatcher.classes["p#handle"] = handle

    result = g.run({"text": "t"})

    assert (result.exit, result.outputs) == ("failed", {"cause": "edge_check", "error_cause": "validation"})
    assert (result.error.cause, result.error.edge, result.error.detail) == (
        "edge_check", SAVE, {"branch_key": SAVE, "error_cause": "validation"})
    assert checker.keys() == [SAVE]                  # a failed check never falls through to the next branch
    [event] = g.events(result.run_id, "edge.check")
    assert (event["take"], event["error_cause"], event["reason"]) == (
        None, "validation", "no valid structured output after 3 attempt(s)")


def test_counters_count_takes_across_a_loop(graph, make_step):
    checker = FakeChecker({"validate.done[fix]": [True, True, False]})
    g = graph(LOOP.replace("max_traversals: 2", "max_traversals: 3"),
              {"validate": make_step(inputs={"text": str}), "fix": make_step()}, edge_checker=checker)

    result = g.run({"text": "t"})

    assert (result.exit, result.outputs) == ("done", {"fixes": 2, "checks": 3})
    assert [(e["branch"], e["taken"]) for e in g.events(result.run_id, "edge.taken")
            if e["from"] == "validate.done"] == [(0, 1), (0, 2), (1, 1)]
    assert len(checker.calls) == 3


def test_an_exhausted_branch_costs_no_check(graph, make_step):
    checker = FakeChecker({"validate.done[fix]": [True, True, True]})
    g = graph(LOOP, {"validate": make_step(inputs={"text": str}), "fix": make_step()}, edge_checker=checker)

    result = g.run({"text": "t"})

    assert (result.error.cause, result.error.edge) == ("max_traversals", "validate.done[fix]")
    assert len(checker.calls) == 2


# --- the real WorkerEdgeChecker and a venv worker with the fake provider -------------------------------------------

def worker_env(script: Any = None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != SCRIPT_VAR}
    if script is not None:
        env[SCRIPT_VAR] = json.dumps(script)
    return env


def verdict(to: str, take: bool, reason: str) -> dict[str, Any]:
    return {"match": {"input": {"transition": {"to": to}}}, "output": {"take": take, "reason": reason}}


@pytest.fixture
def worker_checker():
    """`worker_checker(g, script)`: assign every agentic branch of `g.plan` to an extra `sys.executable` venv (as the
    edge venv rule does) and give `g` a `WorkerEdgeChecker` over a real pool whose workers see `script`."""
    pools: list[WorkerPool] = []

    def attach(g, script: Any = None):
        g.plan.venvs.append(PlanVenv(id="edges", python=sys.executable, steps=[]))
        for pid, pp in g.plan.processes.items():
            for edge in pp.definition.edges:
                for index, branch in enumerate(edge.to):
                    if branch.check is not None:
                        g.plan.edge_venvs[f"{pid}:{branch_key(edge.from_, index, branch.name)}"] = "edges"
        pool = WorkerPool(g.plan, env=worker_env(script))
        pools.append(pool)
        g.edge_checker = WorkerEdgeChecker(pool, g.plan)
        return g

    yield attach
    for pool in pools:
        pool.close()


def test_verdicts_from_the_worker_route_the_process(invoice, worker_checker):
    script = [verdict("save", False, "It is marked PRO FORMA."), verdict("credit", True, "It is a credit note.")]
    g = worker_checker(invoice(valid=True), script)

    result = g.run({"text": "t"})

    assert (result.exit, result.outputs, result.status) == ("credit", {"amount": 120}, "succeeded")
    checks = g.events(result.run_id, "edge.check")
    assert [(c["branch_key"], c["take"], c["reason"], c["provider"], c["tier"], c["model_id"], c["attempts"],
             c["replayed"], c["error_cause"]) for c in checks] == [
        (SAVE, False, "It is marked PRO FORMA.", "fake", "cheap", "fake", 1, False, None),
        (CREDIT, True, "It is a credit note.", "fake", "cheap", "fake", 1, False, None)]
    start = g.events(result.run_id, "worker.start")
    assert [(e["step"], e["venv"]) for e in start] == [(f"edge:{SAVE}", "edges")]
    calls = g.events(result.run_id, "model.call")
    assert [(c["step"], c["span"], c["parent"], c["provider"], c["outcome"]) for c in calls] == [
        (f"edge:{SAVE}", None, None, "fake", "valid"), (f"edge:{CREDIT}", None, None, "fake", "valid")]
    assert all(call["seq"] < check["seq"] for call, check in zip(calls, checks, strict=True))
    assert taken(g, result.run_id)[0] == ("validate.done", 1, "credit", [False, True])
    assert result.usage == Usage(cost_usd=0.0, calls=2)
    assert g.record(result.run_id)["usage"]["calls"] == 2


def test_a_child_declaring_another_provider_checks_with_the_root_provider(graph, make_step, worker_checker):
    parent = """
    kind: process
    name: parent
    entry: sub
    inputs: {text: string}
    outputs: {amount: integer}
    steps: {sub: {use: 'process:child'}}
    edges:
      - {from: sub.done, to: $exit.done, with: {amount: steps.sub.outputs.amount}}
    """
    child = DOC.replace("name: p\n", "name: child\nprovider: anthropic\n")
    validate = make_step(lambda i: {"valid": True, "amount": 7}, inputs={"text": str},
                         done={"valid": bool, "amount": int})
    keep = make_step(lambda i: {"amount": i.amount}, inputs={"amount": int}, done={"amount": int})
    g = graph({"parent": parent, "child": child},
              {"child#validate": validate, "child#save": keep, "child#credit": keep, "child#escalate": make_step()},
              provider="fake")
    assert g.plan.processes["child"].definition.provider == "anthropic"
    worker_checker(g, [verdict("save", True, "A final invoice.")])

    result = g.run({"text": "t"})

    assert (result.exit, result.outputs, result.status) == ("done", {"amount": 7}, "succeeded")
    [check] = g.events(result.run_id, "edge.check")
    assert (check["process"], check["branch_key"], check["provider"], check["take"]) == ("child", SAVE, "fake", True)
    [call] = g.events(result.run_id, "model.call")
    assert (call["step"], call["provider"], call["model_id"]) == (f"edge:{SAVE}", "fake", "fake")
    assert call["parent"] == check["parent"] == g.events(result.run_id, "step.start")[0]["span"]


def test_a_check_failing_in_the_worker_routes_edge_check(invoice, worker_checker):
    g = worker_checker(invoice(valid=True), [verdict("credit", True, "unused")])

    result = g.run({"text": "t"})

    assert (result.exit, result.error.cause, result.error.edge) == ("error", "edge_check", SAVE)
    assert result.error.detail == {"branch_key": SAVE, "error_cause": "model"}
    assert "fake provider: no scripted response" in result.error.message
    [check] = g.events(result.run_id, "edge.check")
    assert (check["take"], check["error_cause"], check["provider"]) == (None, "model", "fake")
    assert result.workspace is not None             # the top-level error handler ran: the workspace is kept


def test_edge_cassettes_record_then_replay_from_the_process_dir(invoice, worker_checker, tmp_path):
    process_dir = tmp_path / "processes" / "p"
    process_dir.mkdir(parents=True)
    staging = tmp_path / "staging"
    script = [verdict("save", False, "It is marked PRO FORMA."), verdict("credit", False, "Not a credit note.")]

    recorder = worker_checker(invoice(valid=True, dirs={"p": str(process_dir)}), script)
    recorded = recorder.run({"text": "t"}, run_id="run-record", cassette_mode="record", record_root=staging)
    written = promote(staging, process_dir / "cassettes")

    replayer = worker_checker(invoice(valid=True, dirs={"p": str(process_dir)}))      # no script: no provider
    replayed = replayer.run({"text": "t"}, run_id="run-replay", cassette_mode="replay")

    assert sorted(p.parent for p in written) == [process_dir / "cassettes" / "edges"] * 2
    assert (recorded.exit, replayed.exit) == ("review", "review")
    assert replayed.outputs == recorded.outputs == {"save_taken": 0, "credit_taken": 0, "else_taken": 1}
    checks = replayer.events(replayed.run_id, "edge.check")
    assert [(c["take"], c["reason"], c["replayed"]) for c in checks] == [
        (False, "It is marked PRO FORMA.", True), (False, "Not a credit note.", True)]
    assert {c["cassette"] for c in replayer.events(replayed.run_id, "model.call")} == {"replay"}

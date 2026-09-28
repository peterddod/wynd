"""The agentic-edge seam (PLAN §5.4, §15 item 28; `$DRAFTS/08 §4.2, §4.7`) with a fake `EdgeChecker`: `when` first,
negative verdicts fall through, checker failures route to the handler, counters, `make_edge_check_call`, and the
executor as the only emitter of `edge.check`."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from wynd.runtime.executor.edges import EdgeCheckError, EdgeCheckResult, EdgeVerdict
from wynd.runtime.usage import Usage
from wynd.spec.lockfiles import EdgeLockEntry, EdgesLock, check_hash

SAVE_CHECK = "The document is a final invoice."
CREDIT_CHECK = "The document is a credit note worth saving."

AGENTIC = f"""
kind: process
name: p
goal: Pay only real invoices.
entry: validate
inputs: {{text: string}}
outputs:
  done: {{amount: integer}}
  review: {{save_taken: integer, else_taken: integer}}
steps: {{validate: {{use: ./steps/validate}}, save: {{use: ./steps/save}}, escalate: {{use: ./steps/escalate}}}}
edges:
  - from: validate.done
    kind: agentic
    to:
      - step: save
        name: save
        when: steps.validate.outputs.valid
        check: {SAVE_CHECK}
        context: [steps.validate.outputs, process.goal]
        with: {{amount: steps.validate.outputs.amount}}
      - step: save
        check: {CREDIT_CHECK}
        with: {{amount: 0}}
      - step: escalate
  - {{from: save.done, to: $exit.done, with: {{amount: steps.save.outputs.amount}}}}
  - from: escalate.done
    to: $exit.review
    with: {{save_taken: 'edges["validate.done"].save.taken', else_taken: 'edges["validate.done"][2].taken'}}
"""


@dataclass
class FakeChecker:
    """`EdgeChecker` double: a verdict (bool) or an `EdgeCheckError` per branch key; emits one model.call."""

    answers: dict[str, bool | EdgeCheckError]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def check(self, call, *, cassette, workspace, timeout_s, on_event) -> EdgeCheckResult:
        self.calls.append({"call": call, "cassette": cassette, "workspace": workspace, "timeout_s": timeout_s})
        on_event({"type": "model.call", "provider": call.provider, "outcome": "valid"})
        answer = self.answers[call.branch_key]
        if isinstance(answer, EdgeCheckError):
            raise answer
        return EdgeCheckResult(
            verdict=EdgeVerdict(take=answer, reason=f"verdict on {call.branch_key}"), attempts=2,
            validation_failures=1, provider=call.provider, tier=call.lock.tier, model_id=call.model_id,
            usage=Usage(input_tokens=5, output_tokens=1, cost_usd=0.01, latency_ms=4.0, calls=1),
            replayed=cassette.mode == "replay", duration_ms=12.0,
        )

    def keys(self) -> list[str]:
        return [c["call"].branch_key for c in self.calls]


@pytest.fixture
def agentic(graph, make_step):
    def make(valid: bool, answers: dict[str, bool | EdgeCheckError], doc: str = AGENTIC, **kwargs):
        validate = make_step(lambda i: {"valid": valid, "amount": 120}, inputs={"text": str},
                             done={"valid": bool, "amount": int})
        save = make_step(lambda i: {"amount": i.amount}, inputs={"amount": int}, done={"amount": int})
        checker = kwargs.pop("edge_checker", None) or FakeChecker(answers)
        return graph(doc, {"validate": validate, "save": save, "escalate": make_step()}, edge_checker=checker,
                     **kwargs)

    return make


def test_a_false_when_costs_no_check(agentic):
    g = agentic(valid=False, answers={"validate.done[1]": True})

    result = g.run({"text": "t"})

    assert (result.exit, result.outputs) == ("done", {"amount": 0})
    assert g.edge_checker.keys() == ["validate.done[1]"]


def test_the_first_positive_verdict_is_taken(agentic):
    g = agentic(valid=True, answers={"validate.done[save]": True})

    result = g.run({"text": "t"})

    assert (result.exit, result.outputs) == ("done", {"amount": 120})
    assert g.edge_checker.keys() == ["validate.done[save]"]
    [check] = g.events(result.run_id, "edge.check")
    assert {k: check[k] for k in ("process", "parent", "edge", "branch", "branch_key", "target", "take", "reason",
                                  "attempts", "validation_failures", "error_cause", "provider", "tier", "model_id",
                                  "duration_ms", "replayed")} == {
        "process": "p", "parent": None, "edge": "validate.done", "branch": 0, "branch_key": "validate.done[save]",
        "target": "save", "take": True, "reason": "verdict on validate.done[save]", "attempts": 2,
        "validation_failures": 1, "error_cause": None, "provider": "fake", "tier": "cheap", "model_id": "fake",
        "duration_ms": 12.0, "replayed": False}
    taken = g.events(result.run_id, "edge.taken")[0]
    assert (taken["branch"], taken["kind"], taken["taken"]) == (0, "agentic", 1)
    assert taken["verdicts"] == [{"branch": 0, "take": True, "reason": "verdict on validate.done[save]"}]
    assert "verdicts" not in g.events(result.run_id, "edge.taken")[1]      # deterministic edge
    assert result.usage == Usage(input_tokens=5, output_tokens=1, cost_usd=0.01, latency_ms=4.0, calls=1)


def test_negative_verdicts_fall_through_and_only_the_taken_branch_counts(agentic):
    g = agentic(valid=True, answers={"validate.done[save]": False, "validate.done[1]": False})

    result = g.run({"text": "t"})

    assert (result.exit, result.outputs) == ("review", {"save_taken": 0, "else_taken": 1})
    assert g.edge_checker.keys() == ["validate.done[save]", "validate.done[1]"]
    checks = g.events(result.run_id, "edge.check")
    assert [(c["branch_key"], c["take"]) for c in checks] == [("validate.done[save]", False),
                                                              ("validate.done[1]", False)]
    taken = g.events(result.run_id, "edge.taken")[0]
    assert (taken["to"], taken["branch"]) == ("escalate", 2)
    assert [v["take"] for v in taken["verdicts"]] == [False, False]
    assert result.usage.calls == 2


def test_no_else_and_a_negative_verdict_routes_no_branch_matched(agentic):
    doc = AGENTIC.replace("      - step: escalate\n", "")
    g = agentic(valid=True, answers={"validate.done[save]": False, "validate.done[1]": False}, doc=doc)

    result = g.run({"text": "t"})

    assert (result.error.cause, result.error.edge) == ("no_branch_matched", "validate.done")
    assert g.events(result.run_id, "edge.taken") == []


def test_a_checker_failure_routes_edge_check_to_the_handler(agentic):
    g = agentic(valid=True, answers={"validate.done[save]": EdgeCheckError("transport", "connection reset")})

    result = g.run({"text": "t"})

    error = result.error
    assert (error.cause, error.step, error.edge) == ("edge_check", "validate", "validate.done[save]")
    assert error.detail == {"branch_key": "validate.done[save]", "error_cause": "transport"}
    assert error.partial_outputs == {"valid": True, "amount": 120}
    assert "connection reset" in error.message
    [check] = g.events(result.run_id, "edge.check")
    assert (check["take"], check["error_cause"], check["reason"], check["provider"]) == (
        None, "transport", "connection reset", "fake")
    assert g.events(result.run_id, "edge.taken") == []


def test_without_a_checker_an_agentic_branch_is_an_edge_check_error(agentic):
    g = agentic(valid=True, answers={})
    g.edge_checker = None

    result = g.run({"text": "t"})

    assert (result.error.cause, result.error.message) == ("edge_check", "agentic edges need an EdgeChecker")
    assert result.error.detail == {"branch_key": "validate.done[save]", "error_cause": "config"}


def test_an_unknown_edge_provider_is_a_config_failure(agentic):
    lock = EdgesLock(edges={"validate.done[save]": EdgeLockEntry(check_hash=check_hash(SAVE_CHECK, None),
                                                                 provider="no-such-provider")})
    g = agentic(valid=True, answers={}, edges_locks={"p": lock})

    result = g.run({"text": "t"})

    assert (result.error.cause, result.error.detail["error_cause"]) == ("edge_check", "config")
    assert g.edge_checker.calls == []
    [check] = g.events(result.run_id, "edge.check")
    assert (check["error_cause"], check["take"], check["provider"]) == ("config", None, None)


def test_the_check_call_carries_the_resolved_lock_provider_model_and_context(agentic, tmp_path):
    entry = EdgeLockEntry(check_hash="sha256:" + "0" * 64, provider="claude-code", tier="strong", timeout_s=30)
    g = agentic(valid=True, answers={"validate.done[save]": True}, edges_locks={"p": EdgesLock(
        edges={"validate.done[save]": entry})})

    result = g.run({"text": "t"}, run_id="run-edge", cassette_mode="replay", cassette_root=tmp_path / "cass",
                   record_root=tmp_path / "staging", cassette_literals={"/abs/tmp": "<tmp>"})

    [sent] = g.edge_checker.calls
    call = sent["call"]
    assert (call.run_id, call.process, call.process_goal, call.edge, call.branch, call.branch_key) == (
        "run-edge", "p", "Pay only real invoices.", "validate.done", 0, "validate.done[save]")
    assert (call.source_step, call.target, call.check, call.bindings) == ("validate", "save", SAVE_CHECK,
                                                                           {"amount": 120})
    assert call.context == {"steps.validate.outputs": {"valid": True, "amount": 120},
                            "process.goal": "Pay only real invoices."}
    assert (call.lock, call.provider, call.model_id) == (entry, "claude-code", "opus")
    assert sent["timeout_s"] == 30
    cassette = sent["cassette"]
    assert (cassette.mode, cassette.dir, cassette.record_dir, cassette.literals) == (
        "replay", str(tmp_path / "cass" / "edges"), str(tmp_path / "staging" / "edges"), {"/abs/tmp": "<tmp>"})
    assert Path(sent["workspace"]).name == "run-edge"
    [model_call] = g.events(result.run_id, "model.call")
    assert (model_call["step"], model_call["span"], model_call["parent"], model_call["outcome"]) == (
        "edge:validate.done[save]", None, None, "valid")
    assert g.events(result.run_id, "edge.check")[0]["replayed"] is True


def test_a_missing_lock_entry_uses_defaults_and_the_source_outputs(agentic):
    g = agentic(valid=False, answers={"validate.done[1]": True})

    g.run({"text": "t"})

    call = g.edge_checker.calls[0]["call"]
    assert call.lock == EdgeLockEntry(check_hash=check_hash(CREDIT_CHECK, None))
    assert (call.lock.tier, call.lock.retries, call.lock.timeout_s) == ("cheap", 2, 120)
    assert (call.provider, call.model_id) == ("fake", "fake")
    assert call.context == {"previous.outputs": {"valid": False, "amount": 120}}
    assert g.edge_checker.calls[0]["timeout_s"] == 120


def test_a_branch_inside_a_child_uses_the_root_provider_and_nests_its_events(graph, make_step):
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
    child = AGENTIC.replace("name: p\n", "name: child\nprovider: claude-code\n")
    validate = make_step(lambda i: {"valid": True, "amount": 7}, inputs={"text": str},
                         done={"valid": bool, "amount": int})
    save = make_step(lambda i: {"amount": i.amount}, inputs={"amount": int}, done={"amount": int})
    checker = FakeChecker({"validate.done[save]": True})
    g = graph({"parent": parent, "child": child},
              {"child#validate": validate, "child#save": save, "child#escalate": make_step()},
              edge_checker=checker, provider="fake")

    result = g.run({"text": "t"})

    assert result.outputs == {"amount": 7}
    assert (checker.calls[0]["call"].provider, checker.calls[0]["call"].process) == ("fake", "child")
    sub_span = g.events(result.run_id, "step.start")[0]["span"]
    [check] = g.events(result.run_id, "edge.check")
    [model_call] = g.events(result.run_id, "model.call")
    assert check["parent"] == model_call["parent"] == sub_span
    ends = {e["step"]: e for e in g.events(result.run_id, "step.end")}
    assert ends["sub"]["usage"]["calls"] == 1 and result.usage.calls == 1


def test_a_check_inside_a_child_spends_the_parent_time_budget(graph, make_step):
    parent = """
    kind: process
    name: parent
    entry: start
    outputs: {amount: integer}
    steps: {start: {use: ./steps/start}, sub: {use: 'process:child'}}
    edges:
      - {from: start.done, to: sub, with: {text: '"t"'}, limits: {timeout: 0.5}}
      - {from: sub.done, to: $exit.done, with: {amount: steps.sub.outputs.amount}}
    """
    child = AGENTIC.replace("name: p\n", "name: child\n")

    class SlowChecker(FakeChecker):
        def check(self, call, *, cassette, workspace, timeout_s, on_event):
            self.calls.append({"call": call, "timeout_s": timeout_s})
            time.sleep(timeout_s + 0.1)
            raise EdgeCheckError("timeout", f"no verdict within {timeout_s:.2f}s")

    validate = make_step(lambda i: {"valid": True, "amount": 7}, inputs={"text": str},
                         done={"valid": bool, "amount": int})
    checker = SlowChecker({})
    g = graph({"parent": parent, "child": child},
              {"start": make_step(), "child#validate": validate, "child#save": make_step(inputs={"amount": int}),
               "child#escalate": make_step()}, edge_checker=checker)

    result = g.run()

    assert 0 < checker.calls[0]["timeout_s"] <= 0.5          # min(lock timeout_s 120, the parent's remaining budget)
    assert (result.error.process, result.error.cause, result.error.step) == ("parent", "timeout", "sub")
    [check] = g.events(result.run_id, "edge.check")
    assert (check["process"], check["error_cause"]) == ("child", "timeout")

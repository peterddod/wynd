"""Executor routing (PLAN §3.5, §5.4; `$DRAFTS/02 §13` test_executor_routing): entry binding, if/elif/else, counters,
the spec scope, limits, `with:` expressions, `$exit`/`$ignore` targets and assembled context."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from wynd.spec.lockfiles import RetryPolicy, StepLock

ONE_STEP = """
kind: process
name: p
entry: a
inputs: {text: string, other: integer}
outputs: {text: string}
steps: {a: {use: ./steps/a}}
edges:
  - from: a.done
    to: $exit.done
    with: {text: steps.a.outputs.text}
"""


def test_entry_binds_only_the_process_inputs_the_entry_declares(graph, make_step):
    echo = make_step(lambda i: {"text": i.text}, inputs={"text": str}, done={"text": str})
    g = graph(ONE_STEP, {"a": echo})

    result = g.run({"text": "hi", "other": 3})

    assert (result.exit, result.outputs, result.status) == ("done", {"text": "hi"}, "succeeded")
    [start] = g.events(result.run_id, "step.start")
    assert start["inputs"] == {"text": "hi"}                  # `other` dropped: the Input forbids extra fields
    assert (start["via"], start["role"], start["id"], start["kind"]) == (None, "node", "p#a", "deterministic")


def test_shorthand_edge_binds_with_expressions(graph, make_step):
    doc = """
    kind: process
    name: p
    entry: a
    inputs: {text: string}
    outputs: {text: string}
    steps: {a: {use: ./steps/a}, b: {use: ./steps/b}}
    edges:
      - from: a.done
        to: b
        with: {text: 'steps.a.outputs.text + "!"', n: 'len(steps.a.outputs.text)', flag: true, label: '"lit"'}
      - from: b.done
        to: $exit.done
        with: {text: steps.b.outputs.text}
    """
    a = make_step(lambda i: {"text": i.text.upper()}, inputs={"text": str}, done={"text": str})
    b = make_step(lambda i: {"text": f"{i.text}/{i.n}/{i.flag}/{i.label}"},
                  inputs={"text": str, "n": int, "flag": bool, "label": str}, done={"text": str})
    g = graph(doc, {"a": a, "b": b})

    result = g.run({"text": "hi"})

    assert result.outputs == {"text": "HI!/2/True/lit"}
    [taken, _] = g.events(result.run_id, "edge.taken")
    assert taken["from"] == "a.done" and taken["to"] == "b" and taken["branch"] == 0 and taken["taken"] == 1
    assert taken["with"] == {"text": "HI!", "n": 2, "flag": True, "label": "lit"}
    b_start = g.events(result.run_id, "step.start")[1]
    assert (b_start["via"], b_start["inputs"]) == ("a.done[0]", taken["with"])


CHAIN = """
kind: process
name: p
entry: classify
inputs: {n: integer}
outputs: {tag: string}
steps:
  classify: {use: ./steps/classify}
  big: {use: ./steps/big}
  mid: {use: ./steps/mid}
  small: {use: ./steps/small}
  never: {use: ./steps/never}
edges:
  - from: classify.done
    to:
      - {step: big, when: steps.classify.outputs.n > 10}
      - {step: mid, when: steps.classify.outputs.n > 5}
      - {step: small}
      - {step: never}
  - {from: big.done, to: $exit.done, with: {tag: '"big"'}}
  - {from: mid.done, to: $exit.done, with: {tag: '"mid"'}}
  - {from: small.done, to: $exit.done, with: {tag: '"small"'}}
  - {from: never.done, to: $exit.done, with: {tag: '"never"'}}
"""


@pytest.mark.parametrize(("n", "tag"), [(20, "big"), (7, "mid"), (1, "small")])
def test_if_elif_else_first_true_wins_and_first_bare_branch_is_the_else(graph, make_step, n, tag):
    classify = make_step(lambda i: {"n": i.n}, inputs={"n": int}, done={"n": int})
    g = graph(CHAIN, {"classify": classify, **{k: make_step() for k in ("big", "mid", "small", "never")}})

    result = g.run({"n": n})

    assert result.outputs == {"tag": tag}
    assert [call.step_id for call in g.dispatcher.calls] == ["p#classify", f"p#{tag}"]


def test_fully_conditioned_to_without_a_match_routes_no_branch_matched(graph, make_step):
    doc = """
    kind: process
    name: p
    entry: v
    outputs: {}
    steps: {v: {use: ./steps/v}, x: {use: ./steps/x}}
    edges:
      - from: v.done
        to:
          - {step: x, when: steps.v.outputs.n > 1}
          - {step: x, when: steps.v.outputs.n < 0}
      - {from: x.done, to: $exit.done}
    """
    g = graph(doc, {"v": make_step(lambda i: {"n": 1}, done={"n": int}), "x": make_step()})

    result = g.run()

    assert (result.exit, result.status) == ("error", "failed")
    error = result.error
    assert (error.cause, error.edge, error.step, error.process) == ("no_branch_matched", "v.done", "v", "p")
    assert error.partial_outputs == {"n": 1}
    assert result.outputs["error"]["cause"] == "no_branch_matched"
    assert g.events(result.run_id, "edge.taken") == []


LOOP = """
kind: process
name: p
entry: validate
inputs: {value: integer}
outputs:
  done: {value: integer}
  given_up: {value: integer, by_index: integer, by_name: integer, fix_runs: integer, validate_runs: integer}
steps: {validate: {use: ./steps/validate}, fix: {use: ./steps/fix}}
edges:
  - from: validate.done
    to:
      - step: $exit.done
        when: steps.validate.outputs.valid
        with: {value: steps.validate.outputs.value}
      - step: fix
        name: retry
        when: steps.validate.outputs.fixable and steps.fix.runs < 3
        with: {value: steps.validate.outputs.value}
        limits: {max_traversals: 10}
      - step: $exit.given_up
        with:
          value: steps.validate.outputs.value
          by_index: 'edges["validate.done"][1].taken'
          by_name: edges["validate.done"].retry.taken
          fix_runs: steps.fix.runs
          validate_runs: steps.validate.runs
  - from: fix.done
    to: validate
    with: {value: steps.fix.outputs.value}
    limits: {max_traversals: 10}
"""


def test_validate_fix_loop_leaves_through_the_else_and_counters_agree(graph, make_step):
    validate = make_step(lambda i: {"valid": False, "fixable": True, "value": i.value},
                         inputs={"value": int}, done={"valid": bool, "fixable": bool, "value": int})
    fix = make_step(lambda i: {"value": i.value + 1}, inputs={"value": int}, done={"value": int})
    g = graph(LOOP, {"validate": validate, "fix": fix})

    result = g.run({"value": 0})

    assert result.exit == "given_up"
    # the latest completed run is what `steps.validate.outputs` means after the loop
    assert result.outputs == {"value": 3, "by_index": 3, "by_name": 3, "fix_runs": 3, "validate_runs": 4}
    # validate's later runs see fix's outputs (latest completed run on the current path)
    assert [p.inputs["value"] for p in g.dispatcher.params_of("p#validate")] == [0, 1, 2, 3]
    assert [p.step_run for p in g.dispatcher.params_of("p#validate")] == [1, 2, 3, 4]
    starts = g.events(result.run_id, "step.start")
    assert [(s["step"], s["run"], s["via"]) for s in starts[:3]] == [
        ("validate", 1, None), ("fix", 1, "validate.done[retry]"), ("validate", 2, "fix.done[0]")]
    retry_takes = [e["taken"] for e in g.events(result.run_id, "edge.taken") if e["name"] == "retry"]
    assert retry_takes == [1, 2, 3]


def test_previous_is_the_most_recently_completed_step(graph, make_step):
    doc = """
    kind: process
    name: p
    entry: a
    outputs: {prev: string, step: string, key: string}
    steps: {a: {use: ./steps/a}}
    edges:
      - from: a.done
        to: $exit.done
        with:
          prev: previous.outputs.text
          step: previous.summary.step
          key: previous.summary.key_outputs.text
    """
    g = graph(doc, {"a": make_step(lambda i: {"text": "from a"}, done={"text": str})})

    assert g.run().outputs == {"prev": "from a", "step": "a", "key": "from a"}


@pytest.mark.parametrize("limit", [2, "1 + 1"])
def test_max_traversals_allows_n_takes_then_routes_to_the_handler(graph, make_step, limit):
    doc = f"""
    kind: process
    name: p
    entry: a
    outputs: {{}}
    steps: {{a: {{use: ./steps/a}}}}
    edges:
      - from: a.done
        to: a
        limits: {{max_traversals: {json.dumps(limit)}}}
    """
    g = graph(doc, {"a": make_step(lambda i: {"k": 1}, done={"k": int})})

    result = g.run()

    assert (result.exit, result.error.cause, result.error.edge) == ("error", "max_traversals", "a.done[0]")
    assert result.error.step == "a" and result.error.partial_outputs == {"k": 1}
    assert [e["taken"] for e in g.events(result.run_id, "edge.taken")] == [1, 2]
    assert len(g.dispatcher.calls) == 3


def test_branch_retries_override_the_target_policy_field_by_field(graph, make_step):
    doc = """
    kind: process
    name: p
    entry: a
    outputs: {}
    steps: {a: {use: ./steps/a}, b: {use: ./steps/b}}
    edges:
      - from: a.done
        to: b
        limits: {retries: {run: 3}}
      - {from: b.done, to: $exit.done}
    """
    lock = StepLock(name="b", kind="deterministic", entrypoint="b:B", retries=RetryPolicy(run=1, validation=0, tool=2))
    g = graph(doc, {"a": make_step(), "b": make_step()}, locks={"p#b": lock})

    g.run()

    [a_params] = g.dispatcher.params_of("p#a")
    [b_params] = g.dispatcher.params_of("p#b")
    assert a_params.policy.retries == RetryPolicy()                         # entry: the kind's default
    assert b_params.policy.retries == RetryPolicy(run=3, validation=0, tool=2)


def test_env_run_id_and_inline_conditionals_in_with(graph, make_step):
    doc = """
    kind: process
    name: p
    entry: a
    outputs: {dest: string, run: string, missing: 'string?', priority: string}
    steps: {a: {use: ./steps/a}}
    edges:
      - from: a.done
        to: $exit.done
        with:
          dest: >-
            if steps.a.outputs.kind == "invoice" then env.INVOICE_DIR
            elif steps.a.outputs.kind == "receipt" then env["RECEIPT_DIR"] else "misc"
          run: run.id
          missing: env.NOT_SET
          priority: if steps.a.outputs.total > 10000 then "high" else "normal"
    """
    a = make_step(lambda i: {"kind": "receipt", "total": 20000}, done={"kind": str, "total": int})
    g = graph(doc, {"a": a}, env={"INVOICE_DIR": "/inv", "RECEIPT_DIR": "/rec"})

    result = g.run(run_id="run-fixed-1")

    assert result.outputs == {"dest": "/rec", "run": "run-fixed-1", "missing": None, "priority": "high"}


EXIT_BIND = """
kind: process
name: p
entry: a
outputs: {record: object, total: number}
steps: {a: {use: ./steps/a}}
edges:
  - from: a.done
    to: $exit.done
    with: WITH
"""


def test_exit_binds_process_outputs_through_with(graph, make_step):
    doc = EXIT_BIND.replace("WITH", "{record: steps.a.outputs, total: steps.a.outputs.total}")
    g = graph(doc, {"a": make_step(lambda i: {"total": 12.5}, done={"total": float})})

    result = g.run()

    assert (result.exit, result.outputs) == ("done", {"record": {"total": 12.5}, "total": 12.5})
    end = g.events(result.run_id, "run.end")[0]
    assert (end["exit"], end["outputs"], end["status"], end["error"]) == ("done", result.outputs, "succeeded", None)


def test_exit_outputs_that_fail_the_process_model_route_invalid_process_outputs(graph, make_step):
    doc = EXIT_BIND.replace("WITH", "{record: steps.a.outputs}")
    g = graph(doc, {"a": make_step(lambda i: {"total": 12.5}, done={"total": float})})

    result = g.run()

    assert (result.exit, result.error.cause, result.error.edge) == ("error", "invalid_process_outputs", "a.done[0]")
    assert "total" in result.error.message


@pytest.mark.parametrize(("site", "text"), [
    ("with", "{total: steps.a.outputs.missing > 1}"),
    ("when", None),
])
def test_an_expression_error_routes_expression_error(graph, make_step, site, text):
    if site == "with":
        doc = EXIT_BIND.replace("WITH", text)
    else:
        doc = EXIT_BIND.replace("to: $exit.done", "to: [{step: $exit.done, when: 'steps.a.outputs.total + \"x\"'}]")
        doc = doc.replace("    with: WITH\n", "")
    g = graph(doc, {"a": make_step(lambda i: {"total": 1.0}, done={"total": float})})

    result = g.run()

    assert (result.exit, result.error.cause, result.error.edge) == ("error", "expression_error", "a.done[0]")
    assert result.error.message.startswith("branch a.done[0]: ")


def test_ignore_target_routes_ignored_exit_after_the_take(graph, make_step):
    doc = """
    kind: process
    name: p
    entry: a
    outputs: {}
    steps: {a: {use: ./steps/a}}
    edges:
      - {from: a.skip, to: $ignore}
      - {from: a.done, to: $exit.done}
    """
    g = graph(doc, {"a": make_step(lambda i: {"exit": "skip"}, done={}, skip={})})

    result = g.run()

    assert (result.exit, result.error.cause, result.error.edge, result.error.step) == (
        "error", "ignored_exit", "a.skip[0]", "a")
    [taken] = g.events(result.run_id, "edge.taken")
    assert (taken["to"], taken["taken"]) == ("$ignore", 1)


def test_an_exit_without_an_edge_routes_unrouted_exit(graph, make_step):
    doc = """
    kind: process
    name: p
    entry: a
    outputs: {}
    steps: {a: {use: ./steps/a}}
    edges:
      - {from: a.done, to: $exit.done}
    """
    g = graph(doc, {"a": make_step(lambda i: {"exit": "odd", "n": 1}, done={}, odd={"n": int})})

    result = g.run()

    assert (result.error.cause, result.error.step, result.error.partial_outputs) == ("unrouted_exit", "a", {"n": 1})
    assert result.error.message == "exit 'odd' of step 'a' has no edge"


def test_agentic_step_receives_its_declared_context(graph, make_step, step_result):
    doc = """
    kind: process
    name: p
    goal: File the invoice.
    entry: a
    inputs: {text: string}
    outputs: {}
    steps: {a: {use: ./steps/a}, b: {use: ./steps/b}}
    edges:
      - {from: a.done, to: b}
      - {from: b.done, to: $exit.done}
    """
    context = ["process.goal", "process.inputs", "previous.summary", "steps.a.outputs", "steps.a.outputs.total",
               "steps.b.outputs", "full_trace"]
    lock = StepLock(name="b", kind="agentic", entrypoint="b:B", context=context, tier="strong")
    a = make_step(lambda i: {"total": 5}, inputs={"text": str}, done={"total": int})
    g = graph(doc, {"a": a, "b": make_step()}, kinds={"p#b": "agentic"}, locks={"p#b": lock},
              overrides={"b": lambda params, emit: step_result("b")})

    g.run({"text": "t"})

    [params] = g.dispatcher.params_of("p#b")
    assert params.context["process.goal"] == "File the invoice."
    assert params.context["process.inputs"] == {"text": "t"}
    assert params.context["previous.summary"] == {"step": "a", "exit": "done", "key_outputs": {"total": 5}, "note": ""}
    assert params.context["steps.a.outputs"] == {"total": 5}
    assert params.context["steps.a.outputs.total"] == 5
    assert params.context["steps.b.outputs"] is None                        # not yet available -> null
    trace = params.context["full_trace"]
    assert trace["process"] == {"name": "p", "goal": "File the invoice.", "inputs": {"text": "t"}}
    assert [(s["step"], s["run"], s["exit"], s["outputs"]) for s in trace["steps"]] == [("a", 1, "done", {"total": 5})]
    policy = params.policy
    assert (policy.kind, policy.provider, policy.tier, policy.model_id) == ("agentic", "fake", "strong", "fake")
    assert g.dispatcher.params_of("p#a")[0].context is None                 # deterministic steps get no context


def test_raw_inputs_and_outputs_are_addressable_in_the_run_workspace(graph, make_step):
    doc = """
    kind: process
    name: p
    entry: a
    inputs: {text: string}
    outputs: {raw: object, inputs: object}
    steps: {a: {use: ./steps/a}, b: {use: ./steps/b}}
    edges:
      - {from: a.done, to: b}
      - from: b.done
        to: $exit.done
        with: {raw: steps.b.outputs.raw, inputs: steps.b.outputs.inputs}
    """

    def read_back(_):                                                      # cwd is the run workspace
        base = Path(".wynd/steps/a/1")
        return {"raw": json.loads((base / "outputs.json").read_text()),
                "inputs": json.loads((base / "inputs.json").read_text())}

    a = make_step(lambda i: {"text": i.text * 2}, inputs={"text": str}, done={"text": str})
    g = graph(doc, {"a": a, "b": make_step(read_back, done={"raw": dict, "inputs": dict})})

    result = g.run({"text": "ab"})

    assert result.outputs == {"raw": {"exit": "done", "text": "abab"}, "inputs": {"text": "ab"}}


def test_an_expression_syntax_error_fails_at_construction(graph, make_step):
    g = graph(EXIT_BIND.replace("WITH", "{total: steps.a.outputs.total}"), {"a": make_step()})
    # a plan read back from JSON (process.lock.yaml) is not syntax-checked by the YAML loader
    g.plan.processes["p"].definition.edges[0].to[0].with_["total"] = "steps.a.outputs.total +"

    with pytest.raises(ValueError, match=r"process p: edges\[0\]\.to\[0\]\.with\.total: "):
        g.executor()

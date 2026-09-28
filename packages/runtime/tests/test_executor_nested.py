"""ProcessStep nodes (SPEC §3.2, §3.5; PLAN §3.5, §5.4; `$DRAFTS/02 §7.7, §13` test_executor_nested): nested
paths and spans, per-instance scopes, error propagation, the root's provider, deadline ownership and usage totals."""

from __future__ import annotations

from wynd.runtime.usage import Usage
from wynd.runtime.worker.client import StepTimeout
from wynd.spec.lockfiles import StepLock

PARENT = """
kind: process
name: parent
entry: sub
inputs: {text: string}
outputs: {text: string}
steps: {sub: {use: 'process:child'}}
edges:
  - {from: sub.done, to: $exit.done, with: {text: steps.sub.outputs.text}}
"""

CHILD = """
kind: process
name: child
entry: read
inputs: {text: string}
outputs: {text: string}
steps: {read: {use: ./steps/read}, shout: {use: ./steps/shout}}
edges:
  - {from: read.done, to: shout, with: {text: steps.read.outputs.text}}
  - {from: shout.done, to: $exit.done, with: {text: steps.shout.outputs.text}}
"""


def test_child_steps_nest_under_the_process_node(graph, make_step):
    read = make_step(lambda i: {"text": i.text}, inputs={"text": str}, done={"text": str})
    shout = make_step(lambda i: {"text": i.text.upper()}, inputs={"text": str}, done={"text": str})
    g = graph({"parent": PARENT, "child": CHILD}, {"child#read": read, "child#shout": shout})

    result = g.run({"text": "hi"})

    assert (result.exit, result.outputs) == ("done", {"text": "HI"})
    starts = g.events(result.run_id, "step.start")
    sub, child_read, child_shout = starts
    assert (sub["step"], sub["id"], sub["kind"], sub["venv"], sub["process"]) == (
        "sub", "process:child", "process", None, "parent")
    assert (child_read["step"], child_read["name"], child_read["process"], child_read["id"]) == (
        "sub.read", "read", "child", "child#read")
    assert child_read["parent"] == child_shout["parent"] == sub["span"] and sub["parent"] is None
    assert {e["run_id"] for e in g.stores.traces.read(result.run_id)} == {result.run_id}
    [child_edge, _, parent_edge] = g.events(result.run_id, "edge.taken")
    assert (child_edge["process"], child_edge["parent"]) == ("child", sub["span"])
    assert (parent_edge["process"], parent_edge["parent"]) == ("parent", None)
    sub_end = g.events(result.run_id, "step.end")[-1]
    assert (sub_end["step"], sub_end["kind"], sub_end["exit"], sub_end["outputs"]) == ("sub", "process", "done",
                                                                                       {"text": "HI"})
    assert [p.step_path for p in g.dispatcher.params_of("child#read")] == ["sub.read"]


def test_each_child_invocation_gets_a_fresh_scope(graph, make_step):
    parent = """
    kind: process
    name: parent
    entry: sub
    outputs: {read_runs: integer, taken: integer, sub_runs: integer}
    steps: {sub: {use: 'process:child'}}
    edges:
      - from: sub.done
        to:
          - {step: sub, when: steps.sub.runs < 2, limits: {max_traversals: 5}}
          - step: $exit.done
            with: {read_runs: steps.sub.outputs.runs, taken: steps.sub.outputs.taken, sub_runs: steps.sub.runs}
    """
    child = """
    kind: process
    name: child
    entry: read
    outputs: {runs: integer, taken: integer}
    steps: {read: {use: ./steps/read}}
    edges:
      - from: read.done
        to: $exit.done
        with: {runs: steps.read.runs, taken: 'edges["read.done"][0].taken + 1'}
    """
    g = graph({"parent": parent, "child": child}, {"child#read": make_step()})

    result = g.run()

    assert result.outputs == {"read_runs": 1, "taken": 1, "sub_runs": 2}
    assert [s["step"] for s in g.events(result.run_id, "step.start")] == ["sub", "sub.read", "sub", "sub.read"]
    assert [p.step_run for p in g.dispatcher.params_of("child#read")] == [1, 1]


FAILING_CHILD = """
kind: process
name: child
entry: read
inputs: {text: string}
outputs: {text: string}
steps: {read: {use: ./steps/read}}
edges:
  - {from: read.done, to: $exit.done, with: {text: steps.read.outputs.text}}
"""


def test_child_error_is_the_node_error_exit_and_the_parent_may_route_it(graph, make_step):
    parent = """
    kind: process
    name: parent
    entry: sub
    inputs: {text: string}
    outputs: {text: string}
    steps: {sub: {use: 'process:child'}, fallback: {use: ./steps/fallback}}
    edges:
      - {from: sub.done, to: $exit.done, with: {text: steps.sub.outputs.text}}
      - from: sub.error
        to: fallback
        with: {cause: steps.sub.outputs.cause, child_cause: steps.sub.outputs.child.cause}
      - {from: fallback.done, to: $exit.done, with: {text: steps.fallback.outputs.text}}
    """

    def fail(_):
        raise RuntimeError("unreadable")

    fallback = make_step(lambda i: {"text": f"{i.cause}/{i.child_cause}"}, inputs={"cause": str, "child_cause": str},
                         done={"text": str})
    g = graph({"parent": parent, "child": FAILING_CHILD},
              {"child#read": make_step(fail, inputs={"text": str}, done={"text": str}), "fallback": fallback})

    result = g.run({"text": "hi"})

    assert (result.exit, result.outputs, result.error) == ("done", {"text": "child_process/step_error"}, None)
    sub_end = [e for e in g.events(result.run_id, "step.end") if e["step"] == "sub"][0]
    child = sub_end["outputs"]["child"]
    assert (sub_end["exit"], sub_end["outputs"]["cause"], sub_end["outputs"]["inputs"]) == (
        "error", "child_process", {"text": "hi"})
    assert (child["process"], child["step"], child["cause"], child["step_error"]["cause"]) == (
        "child", "sub.read", "step_error", "exception")
    [child_error] = g.events(result.run_id, "process.error")
    sub_span = g.events(result.run_id, "step.start")[0]["span"]
    assert (child_error["process"], child_error["parent"], child_error["handler"]) == ("child", sub_span, "default")
    assert result.workspace is None                        # only the top-level handler keeps the workspace


def test_an_unrouted_child_error_reaches_the_parent_handler(graph, make_step):
    def fail(_):
        raise RuntimeError("unreadable")

    g = graph({"parent": PARENT, "child": FAILING_CHILD},
              {"child#read": make_step(fail, inputs={"text": str}, done={"text": str})})

    result = g.run({"text": "hi"})

    assert (result.exit, result.error.process, result.error.step, result.error.cause) == (
        "error", "parent", "sub", "step_error")
    assert result.error.step_error.cause == "child_process"
    assert result.error.step_error.child.step == "sub.read"
    assert result.workspace is not None


def test_a_child_exit_is_the_node_exit(graph, make_step):
    parent = """
    kind: process
    name: parent
    entry: sub
    outputs:
      done: {}
      partial: {got: integer}
    steps: {sub: {use: 'process:child'}}
    edges:
      - {from: sub.done, to: $exit.done}
      - {from: sub.partial, to: $exit.partial, with: {got: steps.sub.outputs.got}}
    """
    child = """
    kind: process
    name: child
    entry: read
    outputs:
      done: {}
      partial: {got: integer}
    steps: {read: {use: ./steps/read}}
    edges:
      - {from: read.done, to: $exit.partial, with: {got: 3}}
    """
    g = graph({"parent": parent, "child": child}, {"child#read": make_step()})

    result = g.run()

    assert (result.exit, result.outputs, result.status) == ("partial", {"got": 3}, "succeeded")


def test_child_inputs_that_fail_validation_are_the_node_error_exit(graph, make_step):
    parent = """
    kind: process
    name: parent
    entry: a
    inputs: {text: string}
    outputs: {text: string}
    steps: {a: {use: ./steps/a}, sub: {use: 'process:child'}}
    edges:
      - {from: a.done, to: sub, with: {text: 42}}
      - {from: sub.done, to: $exit.done, with: {text: steps.sub.outputs.text}}
    """
    g = graph({"parent": parent, "child": CHILD}, {"a": make_step(inputs={"text": str}),
                                                   "child#read": make_step(), "child#shout": make_step()})

    result = g.run({"text": "hi"})

    assert (result.error.cause, result.error.step, result.error.step_error.cause) == (
        "step_error", "sub", "input_validation")
    assert result.error.inputs == {"text": 42}
    assert not g.dispatcher.params_of("child#read")


def test_a_child_provider_is_ignored_for_the_root_default(graph, make_step, step_result):
    child = CHILD.replace("name: child", "name: child\nprovider: claude-code")
    lock = StepLock(name="read", kind="agentic", entrypoint="read:Read")
    g = graph({"parent": PARENT, "child": child},
              {"child#read": make_step(inputs={"text": str}), "child#shout": make_step(inputs={"text": str})},
              provider="fake", kinds={"child#read": "agentic"}, locks={"child#read": lock},
              overrides={"child#read": lambda params, emit: step_result("sub.read", outputs={"text": "x"})})

    g.run({"text": "hi"})

    [params] = g.dispatcher.params_of("child#read")
    assert (params.policy.provider, params.policy.model_id) == ("fake", "fake")


TIMED_PARENT = """
kind: process
name: parent
entry: a
outputs: {}
steps: {a: {use: ./steps/a}, sub: {use: 'process:child'}}
edges:
  - from: a.done
    to: sub
    limits: {timeout: 30}
  - {from: sub.done, to: $exit.done}
"""

TIMED_CHILD = """
kind: process
name: child
entry: read
outputs: {}
steps: {read: {use: ./steps/read}, slow: {use: ./steps/slow}, cleanup: {use: ./steps/cleanup}}
edges:
  - from: read.done
    to: slow
    limits: {timeout: LIMIT}
  - {from: slow.done, to: $exit.done}
finally: [cleanup]
"""


def hang(params, on_event):
    raise StepTimeout("no response")


def test_a_parent_timeout_unwinds_the_child_and_routes_to_the_parent_handler(graph, make_step):
    got = []
    cleanup = make_step(lambda i: got.append(i.model_dump()) or {}, inputs={"exit": str})
    g = graph({"parent": TIMED_PARENT, "child": TIMED_CHILD.replace("LIMIT", "60")},
              {"a": make_step(), "child#read": make_step(), "child#slow": make_step(), "child#cleanup": cleanup},
              overrides={"child#slow": hang})

    result = g.run()

    assert (result.exit, result.error.process, result.error.cause, result.error.step, result.error.edge) == (
        "error", "parent", "timeout", "sub", "a.done[0]")
    assert got == [{"exit": "error"}]                       # the child's finally still ran
    timeouts = {c.step_id: c.timeout for c in g.dispatcher.calls}
    assert 0 < timeouts["child#slow"] <= 30                 # the parent's deadline binds the child (30 < 60)
    assert timeouts["child#cleanup"] is None                # finally runs with no deadline
    timed_out = [(e["step"], e["exit"]) for e in g.events(result.run_id, "step.end") if e["timed_out"]]
    assert timed_out == [("sub.slow", None), ("sub", None)]
    assert [e["process"] for e in g.events(result.run_id, "process.error")] == ["parent"]


def test_a_child_timeout_is_handled_inside_the_child(graph, make_step):
    g = graph({"parent": TIMED_PARENT, "child": TIMED_CHILD.replace("LIMIT", "5")},
              {"a": make_step(), "child#read": make_step(), "child#slow": make_step(),
               "child#cleanup": make_step(inputs={"exit": str})},
              overrides={"child#slow": hang})

    result = g.run()

    assert (result.error.process, result.error.step, result.error.cause) == ("parent", "sub", "step_error")
    child = result.error.step_error.child
    assert (child.process, child.cause, child.step, child.edge) == ("child", "timeout", "sub.slow", "read.done[0]")
    assert 0 < {c.step_id: c.timeout for c in g.dispatcher.calls}["child#slow"] <= 5


def test_usage_totals_sum_nested_steps_once(graph, make_step, step_result):
    parent = """
    kind: process
    name: parent
    entry: a
    inputs: {text: string}
    outputs: {}
    steps: {a: {use: ./steps/a}, sub: {use: 'process:child'}}
    edges:
      - {from: a.done, to: sub, with: {text: '"x"'}}
      - {from: sub.done, to: $exit.done}
    """
    u1 = Usage(input_tokens=100, output_tokens=10, cost_usd=0.25, latency_ms=10.0, calls=1)
    u2 = Usage(input_tokens=7, output_tokens=3, cost_usd=None, latency_ms=5.0, calls=2)
    agentic = {"parent#a": "agentic", "child#read": "agentic"}
    locks = {sid: StepLock(name=sid.split("#")[1], kind="agentic", entrypoint="m:C") for sid in agentic}
    g = graph({"parent": parent, "child": CHILD},
              {"a": make_step(inputs={"text": str}), "child#read": make_step(inputs={"text": str}),
               "child#shout": make_step(lambda i: {"text": i.text}, inputs={"text": str}, done={"text": str})},
              kinds=agentic, locks=locks,
              overrides={"a": lambda params, emit: step_result("a", usage=u1),
                         "child#read": lambda params, emit: step_result("sub.read", outputs={"text": "t"}, usage=u2)})

    result = g.run({"text": "hi"})

    assert result.usage == u1 + u2 == Usage(input_tokens=107, output_tokens=13, cost_usd=0.25, latency_ms=15.0,
                                            calls=3)
    ends = {e["step"]: e["usage"] for e in g.events(result.run_id, "step.end")}
    assert ends["sub"] == u2.model_dump(mode="json") and ends["sub.shout"] is None
    assert g.events(result.run_id, "run.end")[0]["usage"] == result.usage.model_dump(mode="json")
    assert g.record(result.run_id)["usage"] == result.usage.model_dump(mode="json")

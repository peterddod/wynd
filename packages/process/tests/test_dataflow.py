"""Exit-aware dataflow: guard atoms, the fixpoint, WHEN/WITH/FINAL states and the typed sites (PLAN §6.1, §15 item 6;
`$DRAFTS/04 §4.8`, branch-level guards only)."""

from pathlib import Path

import pytest

from wynd.process.validation.dataflow import (
    NOT_RUN,
    ExitIs,
    ExitIsNot,
    HasRun,
    NotRun,
    analyse,
    atom,
    atoms,
    single,
    typed_sites,
)
from wynd.process.validation.exprcheck import site_findings
from wynd.process.validation.structure import StepIface, normalise
from wynd.spec.expr.evaluator import parse
from wynd.spec.process_doc import ProcessDoc
from wynd.spec.proto_step import ProtoStep
from wynd.spec.schemas import ANY, MISSING, schema_at
from wynd.spec.yamlio import parse_model

REPO = Path(__file__).resolve().parents[3]
DOGFOOD = REPO / "examples/invoices/processes/process_supplier_invoice"
E = frozenset({NOT_RUN})


def iface(inputs: dict | None = None, precise: bool = True, **exits: dict) -> StepIface:
    exits = exits or {"done": {}}
    proto = ProtoStep.model_validate({"kind": "proto_step", "name": "x", "instruction": "x", "inputs": inputs or {},
                                      "exits": list(exits), "outputs": exits})
    interface = proto.interface()
    return StepIface(interface.input, interface.with_error(), precise)


def process(text: str) -> ProcessDoc:
    header = "kind: process\nname: p\ninputs: {text: string}\noutputs: {result: string}\n"
    return normalise(parse_model(header + text, ProcessDoc, "p.yaml"))


def findings(doc, ifaces, loc) -> list[str]:
    [site] = [s for s in typed_sites("p", doc, analyse(doc, ifaces), ifaces) if s.loc == loc]
    return [d.code for d in site_findings(site, doc, ifaces)]


def s(*exits: str) -> frozenset[str]:
    return frozenset(exits)


# --- guard atoms ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "expected"), [
    ('steps.x.exit == "done"', ExitIs("x", s("done"))),
    ('"done" == steps.x.exit', ExitIs("x", s("done"))),
    ('steps["x"].exit == "done"', ExitIs("x", s("done"))),
    ('steps.x.exit in ["a", "b"]', ExitIs("x", s("a", "b"))),
    ('steps.x.exit != "done"', ExitIsNot("x", s("done"))),
    ('not (steps.x.exit == "done")', ExitIsNot("x", s("done"))),
    ('not (steps.x.exit in ["a"])', ExitIsNot("x", s("a"))),
    ('steps.x.exit not in ["a", "b"]', ExitIsNot("x", s("a", "b"))),
    ("steps.x.runs > 0", HasRun("x")),
    ("steps.x.runs >= 1", HasRun("x")),
    ("steps.x.runs != 0", HasRun("x")),
    ("0 < steps.x.runs", HasRun("x")),
    ("steps.x.exit != null", HasRun("x")),
    ("steps.x.runs == 0", NotRun("x")),
    ("steps.x.exit == null", NotRun("x")),
    ("not (steps.x.runs > 0)", NotRun("x")),
    ('steps.x.exit == ""', ExitIs("x", s())),       # "" never names an exit: nothing can satisfy it
    ("steps.x.runs < 3", None),
    ("steps.x.runs > 1", None),
    ("steps.x.outputs.valid", None),
    ('steps.x.exit == steps.y.exit', None),
    ('steps.x.exit in "done"', None),
    ('steps.x.outputs.exit == "done"', None),
    ('steps.x.exit == "a" and steps.y.runs > 0', None),
])
def test_atom(text, expected):
    assert atom(parse(text)) == expected


def test_atoms_take_the_top_level_and_chain_only():
    assert atoms('steps.x.exit == "a" and steps.y.runs > 0 and steps.x.outputs.ok') == [
        ExitIs("x", s("a")), HasRun("y"),
    ]
    assert atoms('steps.x.exit == "a" or steps.y.runs > 0') == []
    assert atoms(None) == [] and atoms(True) == []
    assert single('steps.x.exit == "a"') == ExitIs("x", s("a"))
    assert single('steps.x.exit == "a" and steps.y.runs > 0') is None


# --- the fixpoint ---------------------------------------------------------------------------------------------------

LINEAR = """entry: a
steps: {a: {use: ./steps/a}, b: {use: ./steps/b}, c: {use: ./steps/c}}
edges:
  - {from: a.done, to: b, with: {value: steps.a.outputs.value}}
  - {from: b.done, to: c, with: {value: steps.b.outputs.value}}
  - {from: c.done, to: $exit.done, with: {result: steps.c.outputs.value}}
"""


def test_linear():
    doc = process(LINEAR)
    ifaces = {k: iface({"value": "string"}, done={"value": "string"}) for k in "abc"}
    flow = analyse(doc, ifaces)
    assert flow.inputs["a"] == {"a": E, "b": E, "c": E}
    assert flow.inputs["c"] == {"a": s("done"), "b": s("done"), "c": E}
    assert flow.with_[1, 0] == {"a": s("done"), "b": s("done"), "c": E}
    assert findings(doc, ifaces, ("edges", 2, "to", 0, "with", "result")) == []


DIAMOND = """entry: s
steps: {{s: {{use: ./steps/s}}, x: {{use: ./steps/x}}, k: {{use: ./steps/k}}, j: {{use: ./steps/j}}}}
edges:
  - from: s.a
    to: x
  - from: s.b
    to: x
  - from: x.done
    to: j
  - from: x.other
    to: {detour}
  - from: k.done
    to: j
  - from: j.done
    to: $exit.done
    with: {{result: steps.x.outputs.f}}
"""


def diamond_ifaces():
    return {
        "s": iface(a={}, b={}),
        "x": iface(done={"f": "string"}, other={"g": "string"}),
        "k": iface(),
        "j": iface(),
    }


def test_diamond_field_valid_only_if_every_arm_leaves_x_with_the_declaring_exit():
    ifaces = diamond_ifaces()
    doc = process(DIAMOND.format(detour="k"))
    flow = analyse(doc, ifaces)
    assert flow.inputs["j"]["x"] == s("done", "other")
    assert findings(doc, ifaces, ("edges", 5, "to", 0, "with", "result")) == ["E-REF-EXIT"]

    doc = process(DIAMOND.format(detour="$ignore"))
    flow = analyse(doc, ifaces)
    assert flow.inputs["j"]["x"] == s("done")
    assert "k" not in flow.inputs
    assert findings(doc, ifaces, ("edges", 5, "to", 0, "with", "result")) == []


def dogfood():
    doc = normalise(parse_model((DOGFOOD / "process.yaml").read_text(), ProcessDoc, "process.yaml"))
    names = {"read": "read_pdf", "extract": "extract_invoice_fields", "validate": "validate_fields",
             "fix": "fix_fields", "save": "save_record", "escalate": "escalate_to_human"}
    ifaces = {}
    for key, name in names.items():
        proto = parse_model((DOGFOOD / "proto" / f"{name}.yaml").read_text(), ProtoStep, f"{name}.yaml")
        ifaces[key] = StepIface(proto.interface().input, proto.interface().with_error(), True)
    return doc, ifaces


def test_loop_back_edge_dogfood_states():
    doc, ifaces = dogfood()
    flow = analyse(doc, ifaces)
    assert flow.inputs["validate"] == {
        "read": s("done"), "extract": s("done"), "validate": s(NOT_RUN, "done"), "fix": s(NOT_RUN, "done"),
        "save": E, "escalate": E,
    }
    assert flow.with_[3, 2]["fix"] == s(NOT_RUN, "done")      # escalate branch: fix may not have run
    sites = typed_sites("p", doc, flow, ifaces)
    assert not [d for site in sites if site.expr for d in site_findings(site, doc, ifaces)]


def test_escalate_branch_needs_a_guard_for_fix_outputs():
    """`$DRAFTS/04 §4.13`: steps.fix.outputs.* on the escalate branch errors; default() passes."""
    doc, ifaces = dogfood()
    flow = analyse(doc, ifaces)
    sites = typed_sites("p", doc, flow, ifaces)
    [escalate] = [x for x in sites if x.loc == ("edges", 3, "to", 2, "with", "fields")]
    from dataclasses import replace

    from wynd.spec.expr.analysis import check_references

    assert [d.code for d in check_references("steps.fix.outputs.total", escalate.env)] == ["E-REF-UNRUN"]
    assert check_references('default(steps.fix.outputs.total, 0)', escalate.env) == []
    typo = replace(escalate, expr='default(steps.fix.outputs.totl, 0)')
    assert [d.code for d in site_findings(typo, doc, ifaces)] == ["E-REF-FIELD"]   # soft, but still typo-checked
    assert [d.code for d in check_references('coalesce(steps.fix.outputs.totl, 0)', escalate.env)] == [
        "E-REF-FIELD"]


def test_error_edge():
    doc = process("""entry: a
on_error: null
steps: {a: {use: ./steps/a}, h: {use: ./steps/h}}
edges:
  - {from: a.done, to: $exit.done, with: {result: steps.a.outputs.value}}
  - {from: a.error, to: h, with: {why: steps.a.outputs.message, cause: steps.a.outputs.cause}}
  - {from: h.done, to: $exit.done, with: {result: steps.a.outputs.value}}
""")
    ifaces = {"a": iface(done={"value": "string"}), "h": iface({"why": "string", "cause": "string"})}
    flow = analyse(doc, ifaces)
    assert flow.inputs["h"]["a"] == s("error")
    assert findings(doc, ifaces, ("edges", 1, "to", 0, "with", "why")) == []
    assert findings(doc, ifaces, ("edges", 2, "to", 0, "with", "result")) == ["E-REF-FIELD"]


GUARDS = """entry: x
steps: {{x: {{use: ./steps/x}}, t: {{use: ./steps/t}}, u: {{use: ./steps/u}}, y: {{use: ./steps/y}}}}
edges:
  - from: x.done
    to: y
  - from: x.other
    to: y
  - from: y.done
    to:
      - step: t
        when: {when}
        with: {{v: steps.x.outputs.f}}
      - step: u
        with: {{v: steps.x.outputs.f}}
  - {{from: t.done, to: $exit.done, with: {{result: '"t"'}}}}
  - {{from: u.done, to: $exit.done, with: {{result: '"u"'}}}}
"""


def guard_ifaces():
    return {"x": iface(done={"f": "string"}, other={"g": "string"}), "t": iface({"v": "string"}),
            "u": iface({"v": "string"}), "y": iface()}


def test_positive_guard_makes_with_valid_and_the_else_is_negatively_refined():
    doc = process(GUARDS.format(when="'steps.x.exit == \"done\"'"))
    ifaces = guard_ifaces()
    flow = analyse(doc, ifaces)
    assert flow.when[2, 0]["x"] == s("done", "other")
    assert flow.with_[2, 0]["x"] == s("done")
    assert flow.when[2, 1]["x"] == s("other")
    assert findings(doc, ifaces, ("edges", 2, "to", 0, "with", "v")) == []
    assert findings(doc, ifaces, ("edges", 2, "to", 1, "with", "v")) == ["E-REF-FIELD"]


def test_a_conjunction_guard_gives_no_negative_refinement():
    doc = process(GUARDS.format(when="'steps.x.exit == \"done\" and steps.y.runs > 0'"))
    ifaces = guard_ifaces()
    flow = analyse(doc, ifaces)
    assert flow.with_[2, 0]["x"] == s("done")
    assert flow.when[2, 1]["x"] == s("done", "other")
    assert findings(doc, ifaces, ("edges", 2, "to", 1, "with", "v")) == ["E-REF-EXIT"]


def test_no_refinement_inside_one_expression():
    """Intra-expression and/or/if refinement is not implemented (PLAN §6.1): a reference in the `when` itself is
    checked against the unrefined WHEN state."""
    doc = process(GUARDS.format(when="'steps.x.exit == \"done\" and steps.x.outputs.f == \"a\"'"))
    ifaces = guard_ifaces()
    assert findings(doc, ifaces, ("edges", 2, "to", 0, "when")) == ["E-REF-EXIT"]


def test_false_when_and_check_branches():
    doc = process(GUARDS.format(when="false"))
    ifaces = guard_ifaces()
    flow = analyse(doc, ifaces)
    assert "t" not in flow.inputs and (2, 0) not in flow.with_

    doc = process("""entry: x
steps: {x: {use: ./steps/x}, t: {use: ./steps/t}, u: {use: ./steps/u}}
edges:
  - from: x.done
    kind: agentic
    to:
      - {step: t, when: 'steps.x.exit == "done"', check: the value looks right}
      - {step: u}
  - {from: x.other, to: u}
  - {from: t.done, to: $exit.done, with: {result: '"t"'}}
  - {from: u.done, to: $exit.done, with: {result: '"u"'}}
""")
    flow = analyse(doc, ifaces)
    assert flow.when[0, 1]["x"] == s("done")      # a negative verdict says nothing about the when


def test_previous_is_the_source_exit():
    doc = process("""entry: c
steps: {c: {use: ./steps/c}, d: {use: ./steps/d}}
edges:
  - {from: c.ok, to: d, with: {v: previous.outputs.value}}
  - {from: c.bad, to: d, with: {v: previous.outputs.value}}
  - {from: d.done, to: $exit.done, with: {result: previous.summary.note}}
""")
    ifaces = {"c": iface(ok={"value": "string"}, bad={"reason": "string"}), "d": iface({"v": "string"})}
    assert findings(doc, ifaces, ("edges", 0, "to", 0, "with", "v")) == []
    assert findings(doc, ifaces, ("edges", 1, "to", 0, "with", "v")) == ["E-REF-FIELD"]
    assert findings(doc, ifaces, ("edges", 2, "to", 0, "with", "result")) == []


def test_fixpoint_terminates_on_nested_cycles():
    doc = process("""entry: a
steps: {a: {use: ./steps/a}, b: {use: ./steps/b}, c: {use: ./steps/c}, d: {use: ./steps/d}}
edges:
  - from: a.done
    to: b
  - from: b.done
    to: [{step: b, when: steps.b.runs < 3}, {step: c}]
  - from: c.done
    to: [{step: b, when: steps.c.runs < 3}, {step: d, when: steps.d.runs < 2}, {step: a}]
  - from: d.done
    to: [{step: c, when: steps.d.runs < 5}, {step: $exit.done, with: {result: '"x"'}}]
""")
    ifaces = {k: iface() for k in "abcd"}
    flow = analyse(doc, ifaces)
    assert set(flow.inputs) == set("abcd")
    assert flow.inputs["a"] == {"a": s(NOT_RUN, "done"), "b": s(NOT_RUN, "done"), "c": s(NOT_RUN, "done"),
                                "d": s(NOT_RUN, "done")}
    assert flow.inputs["d"]["c"] == s("done")


def test_final_state():
    doc = process("""entry: a
steps: {a: {use: ./steps/a}, b: {use: ./steps/b}, h: {use: ./steps/h}, f: {use: ./steps/f}, z: {use: ./steps/z}}
edges:
  - {from: a.done, to: b}
  - {from: b.done, to: $exit.done, with: {result: '"x"'}}
  - {from: z.done, to: $exit.done, with: {result: '"z"'}}
on_error: h
finally: [f]
""")
    ifaces = {"a": iface(done={}, other={}), "b": iface(), "h": iface(done={}, handled={}), "f": iface(),
              "z": iface()}
    flow = analyse(doc, ifaces)
    assert flow.final == {
        "a": s(NOT_RUN, "error", "done", "other"),   # a.other is unrouted: the handler (then finally) runs after it
        "b": s(NOT_RUN, "error", "done"),
        "h": s(NOT_RUN, "error", "done", "handled"),
        "f": s(NOT_RUN, "error", "done"),
        "z": s(NOT_RUN, "error"),                     # unreachable: never ran
    }


def test_synthetic_sites():
    doc = process("""entry: a
steps: {a: {use: ./steps/a}, h: {use: ./steps/h}, f: {use: ./steps/f}}
edges:
  - {from: a.done, to: $exit.done, with: {result: steps.a.outputs.value}}
on_error: h
finally: [f]
""")
    ifaces = {"a": iface({"text": "string"}, done={"value": "string"}),
              "h": iface({"message": "string"}, done={"result": "string"}),
              "f": iface({"run_id": "string", "text": "string"})}
    sites = typed_sites("p", doc, analyse(doc, ifaces), ifaces)
    by_target = {}
    for site in sites:
        by_target.setdefault(site.target, []).append(site)
    [entry] = by_target["entry"]
    assert (entry.loc, entry.expr, entry.src_schema, entry.dst_schema) == (
        ("inputs", "text"), None, {"type": "string"}, {"type": "string"})
    [exit_site] = by_target["exit"]
    assert exit_site.dst_schema == {"type": "string"} and exit_site.expr == "steps.a.outputs.value"
    [on_error] = by_target["on_error"]
    assert on_error.dst_schema == ifaces["h"].input
    assert schema_at(on_error.src_schema, ("cause",)) not in (ANY, MISSING)
    [handler_exit] = by_target["handler_exit"]
    assert handler_exit.src_schema == ifaces["h"].exits["done"]
    assert {(s.loc, s.src_schema["type"]) for s in by_target["finally"]} == {(("finally", 0), "string")}

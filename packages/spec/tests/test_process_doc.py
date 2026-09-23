"""Process documents ($DRAFTS/01 §6.4, §12.7; PLAN §3.3, §4.1 notes, §15 items 7, 9, 70)."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from wynd.spec import (
    Branch,
    Edge,
    ExprSite,
    FinallyStep,
    Limits,
    ProcessDoc,
    SpecError,
    check_process_doc,
    dump_yaml,
    expression_sites,
    interface_from_fields,
    is_else,
    load_process,
    parse_model,
    site_position,
)

FIXTURES = Path(__file__).parent / "fixtures" / "process"
DOGFOOD = FIXTURES / "dogfood.yaml"


def doc(**extra) -> ProcessDoc:
    data = {"kind": "process", "name": "p", "entry": "a", "steps": {"a": {"use": "./steps/a"},
                                                                    "b": {"use": "./steps/b"}}}
    return ProcessDoc.model_validate({**data, **extra})


def findings(d: ProcessDoc) -> list[tuple[str, tuple]]:
    return [(x.code, x.loc) for x in check_process_doc(d)]


def test_dogfood_loads_checks_clean_and_round_trips(expr_double):
    process = load_process(DOGFOOD)
    assert process.exits == ["done", "not_an_invoice", "needs_review"]
    assert process.effective_provider == "claude-code"
    assert process.env.vars["RECORDS_DIR"] == "Directory for records."
    validate = process.edge("validate.done")
    assert [b.step for b in validate.to] == ["save", "fix", "escalate"]
    assert [is_else(b) for b in validate.to] == [False, False, True]
    assert validate.to[0].with_["dest"] == (
        "if steps.validate.outputs.fields.total > 10000 then env.REVIEW_DIR else env.RECORDS_DIR")
    assert process.edge("read.done").to[0].with_ == {"invoice_text": "steps.read.outputs.text"}
    assert process.examples[0].env["RECORDS_DIR"] == "{tmp}/records"
    assert check_process_doc(process) == []

    authoring = process.to_authoring()
    assert authoring["edges"][0] == {"from": "read.done", "to": "extract",
                                     "with": {"invoice_text": "steps.read.outputs.text"}}
    assert "exits" not in authoring
    again = parse_model(dump_yaml(authoring), ProcessDoc)
    assert again == process


def test_interface_is_built_from_the_declared_fields(expr_double):
    process = load_process(DOGFOOD)
    iface = process.interface()
    assert iface == interface_from_fields(process.inputs, process.outputs)
    assert iface.input == {"type": "object", "properties": {"pdf_path": {"type": "string", "format": "path"}},
                           "required": ["pdf_path"], "additionalProperties": False}
    assert iface.exits == ["done", "not_an_invoice", "needs_review"]
    assert iface.outputs["done"]["required"] == ["exit", "record"]


def test_shorthand_to_carries_edge_level_with_limits_check_and_context():
    edge = Edge.model_validate({"from": "a.done", "to": "b", "with": {"x": "steps.a.outputs.x"},
                                "limits": {"max_traversals": 3, "timeout": 2.5}, "check": "It is final.",
                                "context": ["previous.outputs"], "kind": "agentic"})
    [branch] = edge.to
    assert branch.step == "b" and branch.with_ == {"x": "steps.a.outputs.x"}
    assert branch.limits == Limits(max_traversals=3, timeout=2.5)
    assert (branch.check, branch.context, is_else(branch)) == ("It is final.", ["previous.outputs"], False)
    assert (edge.key, edge.source_step, edge.source_exit, edge.kind) == ("a.done", "a", "done", "agentic")


def test_list_items_that_are_strings_become_branches():
    edge = Edge.model_validate({"from": "a.done", "to": ["b", {"step": "$exit.done", "when": "true"}]})
    assert [(b.step, b.when) for b in edge.to] == [("b", None), ("$exit.done", "true")]
    assert edge.to[1].exit_target == "done" and edge.to[0].exit_target is None


@pytest.mark.parametrize(
    ("edge", "message"),
    [
        ({"from": "a.done"}, "to: is required"),
        ({"from": "a.done", "to": []}, "to: must not be an empty list"),
        ({"from": "a.done", "to": {"step": "b"}}, "(got a mapping)"),
        ({"from": "a.done", "to": ["b"], "with": {"x": "1"}}, "with: go on each branch when to: is a list"),
        ({"from": "a.done", "to": ["b"], "limits": {}, "check": "x"}, "limits:/check: go on each branch"),
    ],
)
def test_to_errors(edge, message):
    with pytest.raises(ValidationError) as err:
        Edge.model_validate(edge)
    [error] = err.value.errors()
    assert error["type"] == "E-TO" and message in error["msg"]


def test_edge_from_format_and_branch_names():
    with pytest.raises(ValidationError):
        Edge.model_validate({"from": "a", "to": "b"})
    with pytest.raises(ValidationError):
        Branch.model_validate({"step": "b", "name": "1st"})
    assert Branch.model_validate({"step": "b", "name": "retry"}).name == "retry"


def test_is_else():
    assert is_else(Branch(step="b"))
    assert not is_else(Branch(step="b", when="x"))
    assert not is_else(Branch(step="b", when=False))
    assert not is_else(Branch(step="b", check="the invoice is final"))


def test_yaml_dates_under_with_become_quoted_expressions():
    process = parse_model(
        "kind: process\nname: p\nentry: a\nsteps: {a: {use: ./steps/a}}\n"
        "edges:\n  - from: a.done\n    to: $exit.done\n    with: {due: 2026-10-01, at: 2026-10-01T10:00:00Z, "
        "list: [2026-01-02], n: 3, flag: true, none: null}\n"
        "finally:\n  - step: a\n    with: {when: 2026-12-31}\n",
        ProcessDoc,
    )
    assert process.edges[0].to[0].with_ == {"due": "'2026-10-01'", "at": "'2026-10-01T10:00:00+00:00'",
                                            "list": ["'2026-01-02'"], "n": 3, "flag": True, "none": None}
    assert process.finally_[0].with_ == {"when": "'2026-12-31'"}
    assert ProcessDoc.model_validate_json(process.model_dump_json(by_alias=True)) == process


def test_finally_items_accept_step_keys_and_render_back():
    process = doc(finally_=["b", {"step": "a", "with": {"run": "run.id"}}])
    assert process.finally_ == [FinallyStep(step="b"), FinallyStep(step="a", with_={"run": "run.id"})]
    assert process.to_authoring()["finally"] == ["b", {"step": "a", "with": {"run": "run.id"}}]


def test_use_limits_and_retries_are_validated():
    with pytest.raises(ValidationError) as err:
        doc(steps={"a": {"use": "../a"}})
    assert err.value.errors()[0]["type"] == "E-USE"
    for limits in ({"max_traversals": 0}, {"max_traversals": True}, {"timeout": 0}, {"timeout": -1.5}):
        with pytest.raises(ValidationError) as err:
            Limits.model_validate(limits)
        assert err.value.errors()[0]["type"] == "E-SCHEMA"
    assert Limits.model_validate({"max_traversals": "steps.a.runs + 1", "timeout": "env.T"}).timeout == "env.T"
    with pytest.raises(ValidationError):
        Limits.model_validate({"retries": {"run": -1}})
    with pytest.raises(ValidationError):
        doc(steps={})


def test_expression_sites():
    process = doc(
        edges=[
            {"from": "a.done", "to": [
                {"step": "b", "when": "steps.a.outputs.ok", "with": {"x": "steps.a.outputs.x",
                                                                     "deep": {"l": ["run.id", 3]}, "n": 1},
                 "limits": {"max_traversals": "2 + 1", "timeout": 5}},
                {"step": "$exit.done", "when": True},
            ]},
        ],
        finally_=[{"step": "b", "with": {"id": "run.id"}}],
    )
    assert expression_sites(process) == [
        ExprSite(("edges", 0, "to", 0, "when"), "steps.a.outputs.ok", "when", "a.done", 0),
        ExprSite(("edges", 0, "to", 0, "with", "x"), "steps.a.outputs.x", "with", "a.done", 0),
        ExprSite(("edges", 0, "to", 0, "with", "deep", "l", 0), "run.id", "with", "a.done", 0),
        ExprSite(("edges", 0, "to", 0, "limits", "max_traversals"), "2 + 1", "limit", "a.done", 0),
        ExprSite(("finally", 0, "with", "id"), "run.id", "finally_with", None, None),
    ]


def test_site_position_for_shorthand_and_list_edges(expr_double):
    process = load_process(DOGFOOD)
    assert site_position(process, ("edges", 0, "to", 0, "with", "invoice_text")).line == 39
    assert site_position(process, ("edges", 3, "to", 0, "with", "dest")).line == 51
    assert site_position(process, ("edges", 2, "to", 0, "step")).line == 44
    assert site_position(doc(), ("entry",)) is None


def test_check_codes_are_located_in_the_source(expr_double):
    process = parse_model(
        "kind: process\nname: p\nentry: a\nsteps: {a: {use: ./steps/a}}\non_error: a\n", ProcessDoc, "p.yaml")
    [diag] = check_process_doc(process)
    assert diag.format() == "p.yaml:5:11: error[E-HANDLER-ROLE] on_error: on_error step 'a' must not be the entry step"


def test_check_expression_findings_carry_span_and_position(expr_double):
    process = parse_model(
        "kind: process\nname: p\nentry: a\nsteps: {a: {use: ./steps/a}}\n"
        "edges:\n  - from: a.done\n    to:\n      - step: $exit.done\n        when: x and nofunc(1)\n",
        ProcessDoc,
        "p.yaml",
    )
    [diag] = check_process_doc(process)
    assert (diag.code, diag.line, diag.column, diag.span) == ("E-EXPR-FUNC", 9, 21, (6, 12))
    assert diag.loc == ("edges", 0, "to", 0, "when")


def test_examples_are_checked_against_the_process_models(expr_double):
    process = doc(
        inputs={"pdf_path": "path"},
        outputs={"done": {"record": "object"}, "skipped": {}},
        examples=[
            {"inputs": {"pdf_path": "examples/a.pdf"}, "env": {"DIR": "{tmp}/out"}, "outputs": {"record": {}}},
            {"inputs": {"pdf_path": "examples/a.pdf"}, "exit": "error"},
            {"inputs": {}, "outputs": {"record": "nope"}},
        ],
        edges=[{"from": "a.done", "to": "$exit.done", "with": {"record": "steps.a.outputs"}},
               {"from": "a.skipped", "to": "$exit.skipped"}],
    )
    assert findings(process) == [
        ("E-EXAMPLE", ("examples", 2, "inputs", "pdf_path")),
        ("E-EXAMPLE", ("examples", 2, "outputs", "record")),
    ]


def test_outputs_ambiguous_on_a_process(expr_double):
    process = doc(outputs={"ok": {"a": "string"}})
    assert findings(process) == [("W-OUTPUTS-AMBIGUOUS", ("outputs",))]


def test_ignore_as_the_single_unconditioned_branch_is_fine(expr_double):
    process = doc(edges=[{"from": "a.done", "to": "$ignore"}, {"from": "a.error", "to": ["b"]}])
    assert findings(process) == []


def test_load_errors_are_located(tmp_path, expr_double):
    path = tmp_path / "process.yaml"
    path.write_text("kind: process\nname: p\nentry: a\nsteps:\n  a: {use: ./steps/a}\n  b: {usee: ./steps/b}\n")
    with pytest.raises(SpecError) as err:
        load_process(path)
    assert [d.format().replace(str(path), "P") for d in err.value.diagnostics] == [
        "P:6:6: error[E-SCHEMA] steps.b.use: missing required field 'use'",
        "P:6:13: error[E-SCHEMA] steps.b.usee: unknown field 'usee' (did you mean 'use'?)",
    ]


def test_json_round_trip_of_the_normalised_model(expr_double):
    process = load_process(DOGFOOD)
    data = json.loads(process.model_dump_json(by_alias=True))
    assert data["edges"][0]["to"][0]["with"] == {"invoice_text": "steps.read.outputs.text"}
    # examples keep YAML dates as date objects; in JSON they are ISO strings, which is how every consumer compares
    assert ProcessDoc.model_validate(data).model_dump(mode="json") == process.model_dump(mode="json")
    assert ProcessDoc.model_validate(data).edges == process.edges

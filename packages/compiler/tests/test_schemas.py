"""schemas: models block vs spec build_models, canonical examples, plain language, schema inference."""

from datetime import date
from types import SimpleNamespace

import pytest

from wynd.compiler.calls import ExitFields, Field, InferSchemaResponse
from wynd.compiler.schemas import (
    baseline_fields,
    canonical,
    exit_class_name,
    infer_interface,
    models_imports,
    plain_interface,
    render_models_block,
    schema_from_response,
)
from wynd.spec.interface import interface_from_fields, interface_from_models, interfaces_equivalent
from wynd.spec.proto_step import Example
from wynd.spec.typelang import TScalar, parse_type


def fields(spec: dict) -> dict:
    return {name: parse_type(t) for name, t in spec.items()}


def exec_models(inputs, outputs):
    """Render the block into a class, exec it in a scratch namespace (test only) and return the class."""
    source = "\n".join(models_imports(inputs, outputs)) + "\n\n\nclass S:\n" + render_models_block(inputs, outputs)
    namespace: dict = {}
    exec(compile(source, "<models>", "exec"), namespace)
    return namespace["S"]


CASES = [
    pytest.param(
        {"invoice_text": "string"},
        {"done": {"supplier": "string", "total": "number", "due_date": "date"}, "not_an_invoice": {}},
        id="two-exits",
    ),
    pytest.param(
        {"n": "integer", "flag": "boolean", "at": "datetime", "file": "path", "meta": "object", "tags": "list[string]"},
        {"done": {"note": "string?", "count": "integer?", "items": "list[number]?"}},
        id="scalars-and-optional",
    ),
    pytest.param(
        {"fields": {"supplier": "string", "total": "number"},
         "errors": [{"field": "string", "code": "string", "fixable": "boolean"}]},
        {"done": {"record": {"key": "string", "inner": {"x": "date"}}, "path": "path"}},
        id="nested-and-records",
    ),
    pytest.param({}, {"input": {"a": "string"}, "output": {}}, id="exit-name-collisions"),
]


@pytest.mark.parametrize(("inputs", "outputs"), CASES)
def test_models_block_matches_build_models(inputs, outputs):
    inputs = fields(inputs)
    outputs = {exit: fields(f) for exit, f in outputs.items()}
    cls = exec_models(inputs, outputs)
    rendered = interface_from_models(cls.Input, cls.Output)
    assert interfaces_equivalent(interface_from_fields(inputs, outputs), rendered) == []


def test_models_block_text():
    block = render_models_block(fields({"invoice_text": "string"}),
                                {"done": fields({"total": "number", "due_date": "date?"}), "not_an_invoice": {}})
    assert block == (
        "    class Input(BaseModel):\n"
        '        model_config = ConfigDict(extra="forbid")\n'
        "        invoice_text: str\n"
        "\n"
        "    class Done(BaseModel):\n"
        '        model_config = ConfigDict(extra="forbid")\n'
        '        exit: Literal["done"] = "done"\n'
        "        total: float\n"
        "        due_date: date | None = None\n"
        "\n"
        "    class NotAnInvoice(BaseModel):\n"
        '        model_config = ConfigDict(extra="forbid")\n'
        '        exit: Literal["not_an_invoice"] = "not_an_invoice"\n'
        "\n"
        "    Output = Done | NotAnInvoice\n"
    )


def test_nested_models_are_quoted_and_validate():
    inputs = fields({"errors": [{"field": "string"}], "fields": {"total": "number"}})
    cls = exec_models(inputs, {"done": {}})
    block = render_models_block(inputs, {"done": {}})
    assert 'errors: "list[InputErrorsItem]"' in block
    assert 'fields: "InputFields"' in block
    value = cls.Input.model_validate({"errors": [{"field": "total"}], "fields": {"total": 3}})
    assert value.fields.total == 3.0
    with pytest.raises(ValueError):
        cls.Input.model_validate({"errors": [], "fields": {"total": 1, "extra": 2}})


def test_done_only_output_and_imports():
    inputs, outputs = fields({"p": "path", "d": "date"}), {"done": fields({"o": "object"})}
    assert render_models_block(inputs, outputs).rstrip().endswith("Output = Done")
    assert models_imports(inputs, outputs) == [
        "from datetime import date",
        "from pathlib import Path",
        "from typing import Any, Literal",
        "",
        "from pydantic import BaseModel, ConfigDict",
    ]


def test_exit_class_names():
    assert exit_class_name("not_an_invoice") == "NotAnInvoice"
    assert exit_class_name("input") == "InputExit"
    assert exit_class_name("output") == "OutputExit"


def test_canonical_coerces_and_keeps_given_keys():
    inputs = fields({"when": "date", "amount": "number", "dest": "path", "rows": [{"at": "datetime"}]})
    outputs = {"done": fields({"total": "number", "n": "integer", "ok": "boolean", "note": "string"})}
    example = Example(
        inputs={"when": date(2026, 10, 1), "amount": 12, "dest": "{tmp}/./out/",
                "rows": [{"at": "2026-10-01T09:00:00Z"}]},
        outputs={"total": 1200, "ok": True},
        exit="done",
        description="why",
    )
    out = canonical(example, inputs, outputs)
    assert out.inputs == {"when": "2026-10-01", "amount": 12.0, "dest": "{tmp}/./out/",
                          "rows": [{"at": "2026-10-01T09:00:00Z"}]}
    assert isinstance(out.inputs["amount"], float)
    assert out.outputs == {"total": 1200.0, "ok": True}          # partial outputs stay partial
    assert out.description == "why" and out.exit == "done"


def test_canonical_keeps_nonconforming_and_unknown_values():
    inputs = fields({"when": "date"})
    out = canonical(Example(inputs={"when": "not a date", "extra": date(2026, 1, 2)}), inputs, {"done": {}})
    assert out.inputs == {"when": "not a date", "extra": "2026-01-02"}


def test_canonical_error_exit_outputs_untyped():
    out = canonical(Example(inputs={}, outputs={"x": date(2026, 1, 2)}, exit="error"), {}, {"done": {}})
    assert out.outputs == {"x": "2026-01-02"}


def test_plain_interface_lines():
    lines = plain_interface(
        "fix_fields",
        fields({"invoice_text": "string", "errors": "list[string]"}),
        {"done": fields({"total": "number"}), "cannot_fix": fields({"reason": "string"}), "skipped": {}},
        descriptions={"inputs.invoice_text": "the invoice text", "cannot_fix.reason": "why not"},
        free_text=["reason"],
    )
    assert lines == [
        "fix_fields takes:",
        "  - invoice_text — text: the invoice text",
        "  - errors — a list of text",
        "It finishes in one of 3 ways:",
        "  - done, giving back:",
        "    - total — a number",
        "  - cannot_fix, giving back:",
        "    - reason — text: why not (free text: tests check it is present, not its wording)",
        "  - skipped, giving back nothing",
    ]
    assert plain_interface("s", {}, {"done": {}}) == ["s takes no inputs.", "It finishes in one way:",
                                                       "  - done, giving back nothing"]


def test_baseline_fields():
    examples = [Example(inputs={"a": "x", "n": 1}, outputs={"v": 1.5}), Example(inputs={"a": "y"}, exit="other")]
    inputs, outputs = baseline_fields(examples, ["done", "other"])
    assert inputs == {"a": TScalar("string"), "n": TScalar("integer", optional=True)}
    assert outputs == {"done": {"v": TScalar("number")}, "other": {}}


def response(inputs, exits, problems=()):
    return InferSchemaResponse(
        inputs=[Field(name=n, type=t, description=f"the {n}", free_text=False) for n, t in inputs],
        exits=[ExitFields(exit=x, fields=[Field(name=n, type=t, description="", free_text=free) for n, t, free in f])
               for x, f in exits],
        problems=list(problems),
    )


def test_schema_from_response_collects_problems():
    resp = response([("a", "strng")], [("done", [("note", "string", True)]), ("bogus", [])])
    result = schema_from_response(resp, ["done"])
    assert result.inputs == {}
    assert result.outputs == {"done": {"note": TScalar("string")}}
    assert result.free_text == ["note"]
    assert any("inputs.a" in p and "unknown type" in p for p in result.problems)
    assert any("bogus" in p for p in result.problems)


class FakeLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def call(self, kind, *, node, system, prompt, response_model, tier, thinking):
        self.calls.append(SimpleNamespace(kind=kind, node=node, system=system, prompt=prompt,
                                          model=response_model, tier=tier, thinking=thinking))
        return SimpleNamespace(value=self.answers.pop(0))


EXAMPLES = [Example(inputs={"text": "£12.50"}, outputs={"amount": 12.5}),
            Example(inputs={"text": "EUR 3"}, outputs={"amount": 3})]


def test_infer_interface_recalls_once_with_problems():
    bad = response([("text", "string")], [("done", [("amount", "date", False)])])
    good = response([("text", "string")], [("done", [("amount", "number", False)])])
    llm = FakeLLM(bad, good)
    result = infer_interface(llm, node="parse", name="parse_amount", instruction="Parse it.", exits=["done"],
                             examples=EXAMPLES, guidance=["amounts are numbers"])
    assert result.problems == []
    assert result.outputs == {"done": {"amount": TScalar("number")}}
    assert result.descriptions["inputs.text"] == "the text"
    assert [(c.kind, c.node, c.tier, c.thinking, c.model) for c in llm.calls] == [
        ("infer_schema", "parse", "standard", "low", InferSchemaResponse)] * 2
    assert "## Problems with your previous answer" not in llm.calls[0].prompt
    assert "## Problems with your previous answer" in llm.calls[1].prompt
    assert "amounts are numbers" in llm.calls[0].prompt
    assert "Task: infer the input and output schema" in llm.calls[0].system


def test_infer_interface_reports_remaining_problems():
    bad = response([("text", "string")], [("done", [("amount", "date", False)])])
    llm = FakeLLM(bad, bad)
    result = infer_interface(llm, node="parse", name="parse_amount", instruction="Parse it.", exits=["done"],
                             examples=EXAMPLES)
    assert len(llm.calls) == 2
    assert result.problems and all(p.startswith("example ") for p in result.problems)


def test_infer_interface_model_problems_and_declared_half():
    resp = response([("text", "integer")], [("done", [("amount", "number", False)])], problems=["examples 1 and 2 "
                                                                                                "disagree"])
    llm = FakeLLM(resp)
    result = infer_interface(llm, node="parse", name="parse_amount", instruction="Parse it.", exits=["done"],
                             examples=EXAMPLES, declared_inputs=fields({"text": "string"}))
    assert len(llm.calls) == 1
    assert result.problems == ["examples 1 and 2 disagree"]
    assert result.inputs == {"text": TScalar("string")}                  # the declared half wins
    assert "## Given schema" in llm.calls[0].prompt

"""Schema inference, canonical examples and the models-block source rendering (`$DRAFTS/05 §7.3-§7.4, §9.5`).

The models block mirrors `wynd.spec.typelang.build_models`: `string→str`, `number→float`, `integer→int`,
`boolean→bool`, `date→date`, `datetime→datetime`, `path→Path`, `object→dict[str, Any]`, `list[T]→list[T]`, a nested
mapping → a nested model `<Parent><Field>`, a `[mapping]` list → `<Parent><Field>Item`, `T?` → `T | None = None`.
Nested models are sibling classes in the step class body, so annotations that name them are quoted (a class body
cannot see its enclosing class's names; pydantic resolves the string from the enclosing namespace).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter, ValidationError
from pydantic_core import to_jsonable_python

from wynd.spec.base import RESERVED_EXIT
from wynd.spec.proto_step import Example, check_examples
from wynd.spec.typelang import (
    TList,
    TObject,
    TScalar,
    TypeNode,
    build_models,
    describe_type,
    infer_fields,
    parse_type,
    render_type,
)

if TYPE_CHECKING:
    from wynd.compiler.llm import CompilerLLM
    from wynd.spec.interface import Interface

FREE_TEXT_NOTE = "free text: tests check it is present, not its wording"

_PY_SCALARS = {
    "string": "str",
    "number": "float",
    "integer": "int",
    "boolean": "bool",
    "date": "date",
    "datetime": "datetime",
    "path": "Path",
    "object": "dict[str, Any]",
}
_ADAPTERS = {
    "number": TypeAdapter(float),
    "integer": TypeAdapter(int),
    "boolean": TypeAdapter(bool),
    "date": TypeAdapter(date),
    "datetime": TypeAdapter(datetime),
}


def pascal(text: str) -> str:
    """`extract_invoice_fields` -> `ExtractInvoiceFields`."""
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"[^A-Za-z0-9]+", text) if part)


def exit_class_name(exit: str) -> str:
    """PascalCase(exit), with `Exit` appended when that collides with `Input` or `Output`."""
    name = pascal(exit)
    return name + "Exit" if name in ("Input", "Output") else name


# --- models block ---------------------------------------------------------------------------------------------------

def render_models_block(inputs: Mapping[str, TypeNode], outputs: Mapping[str, Mapping[str, TypeNode]]) -> str:
    """The `Input` model, one model per exit and `Output`, indented for a step class body (four spaces). Codegen
    must include it verbatim; the interface check compares what the step venv describes with these fields."""
    classes: list[str] = []
    _render_model("Input", inputs, None, classes)
    names = []
    for exit, fields in outputs.items():
        name = exit_class_name(exit)
        names.append(name)
        _render_model(name, fields, exit, classes)
    classes.append(f"Output = {' | '.join(names)}")
    return "\n\n".join(_indent(block, "    ") for block in classes) + "\n"


def _render_model(name: str, fields: Mapping[str, TypeNode], exit: str | None, classes: list[str]) -> None:
    lines = [f"class {name}(BaseModel):", '    model_config = ConfigDict(extra="forbid")']
    if exit is not None:
        lines.append(f'    exit: Literal["{exit}"] = "{exit}"')
    for field_name, node in fields.items():
        annotation, quoted = _annotation(node, name + pascal(field_name), classes)
        if quoted:
            annotation = f'"{annotation}"'
        lines.append(f"    {field_name}: {annotation}" + (" = None" if node.optional else ""))
    classes.append("\n".join(lines))


def _annotation(node: TypeNode, nested_name: str, classes: list[str]) -> tuple[str, bool]:
    """(annotation text, names a nested model). Nested models are appended to `classes` before their user."""
    match node:
        case TScalar():
            text, quoted = _PY_SCALARS[node.name], False
        case TList():
            item, quoted = _annotation(node.item, nested_name + "Item", classes)
            text = f"list[{item}]"
        case TObject():
            _render_model(nested_name, dict(node.fields), None, classes)
            text, quoted = nested_name, True
    return (f"{text} | None" if node.optional else text), quoted


def _indent(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def models_imports(inputs: Mapping[str, TypeNode], outputs: Mapping[str, Mapping[str, TypeNode]]) -> list[str]:
    """The import lines the models block needs, in isort order."""
    scalars: set[str] = set()
    for node in [*inputs.values(), *(n for fields in outputs.values() for n in fields.values())]:
        _collect_scalars(node, scalars)
    lines = []
    dates = [name for name in ("date", "datetime") if name in scalars]
    if dates:
        lines.append(f"from datetime import {', '.join(dates)}")
    if "path" in scalars:
        lines.append("from pathlib import Path")
    lines.append("from typing import Any, Literal" if "object" in scalars else "from typing import Literal")
    lines.append("")
    lines.append("from pydantic import BaseModel, ConfigDict")
    return lines


def _collect_scalars(node: TypeNode, out: set[str]) -> None:
    match node:
        case TScalar():
            out.add(node.name)
        case TList():
            _collect_scalars(node.item, out)
        case TObject():
            for _, sub in node.fields:
                _collect_scalars(sub, out)


# --- canonical examples ---------------------------------------------------------------------------------------------

def canonical(
    example: Example, inputs: Mapping[str, TypeNode], outputs: Mapping[str, Mapping[str, TypeNode]]
) -> Example:
    """Values coerced through their types and dumped in JSON mode (dates become ISO strings), keeping only the keys the
    example gave. Strings of `path`/`string` fields (including `{tmp}` values) are kept verbatim. A value that does
    not conform is kept as given (the conformance check reports it)."""
    exit_fields = outputs.get(example.exit, {})
    return example.model_copy(update={
        "inputs": {k: canonical_value(inputs.get(k), v) for k, v in example.inputs.items()},
        "outputs": {k: canonical_value(exit_fields.get(k), v) for k, v in example.outputs.items()},
    })


def canonical_value(node: TypeNode | None, value: Any) -> Any:
    if value is None or node is None:
        return to_jsonable_python(value)
    match node:
        case TScalar(name="string" | "path") if isinstance(value, str):
            return value
        case TScalar(name=name) if name in _ADAPTERS:
            adapter = _ADAPTERS[name]
            try:
                return adapter.dump_python(adapter.validate_python(value), mode="json")
            except ValidationError:
                return to_jsonable_python(value)
        case TList() if isinstance(value, list):
            return [canonical_value(node.item, item) for item in value]
        case TObject() if isinstance(value, Mapping):
            fields = dict(node.fields)
            return {k: canonical_value(fields.get(k), v) for k, v in value.items()}
    return to_jsonable_python(value)


# --- plain language -------------------------------------------------------------------------------------------------

def plain_interface(
    name: str,
    inputs: Mapping[str, TypeNode],
    outputs: Mapping[str, Mapping[str, TypeNode]],
    *,
    descriptions: Mapping[str, str] | None = None,
    free_text: Sequence[str] = (),
) -> list[str]:
    """The schema as the process owner reads it. `descriptions` keys are `inputs.<field>` and `<exit>.<field>`."""
    descriptions = descriptions or {}
    lines = [f"{name} takes:" if inputs else f"{name} takes no inputs."]
    lines += [_field_line(f, node, descriptions.get(f"inputs.{f}"), False) for f, node in inputs.items()]
    if len(outputs) == 1:
        lines.append("It finishes in one way:")
    else:
        lines.append(f"It finishes in one of {len(outputs)} ways:")
    for exit, fields in outputs.items():
        if not fields:
            lines.append(f"  - {exit}, giving back nothing")
            continue
        lines.append(f"  - {exit}, giving back:")
        lines += ["  " + _field_line(f, node, descriptions.get(f"{exit}.{f}"), f in free_text)
                  for f, node in fields.items()]
    return lines


def _field_line(name: str, node: TypeNode, description: str | None, free: bool) -> str:
    line = f"  - {name} — {describe_type(node)}"
    if description:
        line += f": {description}"
    if free:
        line += f" ({FREE_TEXT_NOTE})"
    return line


# --- schema inference -----------------------------------------------------------------------------------------------

@dataclass
class InferredSchema:
    inputs: dict[str, TypeNode]
    outputs: dict[str, dict[str, TypeNode]]
    descriptions: dict[str, str] = field(default_factory=dict)   # "inputs.<f>" | "<exit>.<f>" -> plain text
    free_text: list[str] = field(default_factory=list)          # output fields tested for presence only
    problems: list[str] = field(default_factory=list)           # non-empty: ask a clarification, do not compile


def baseline_fields(examples: Sequence[Example], exits: Sequence[str]) -> tuple[dict[str, TypeNode],
                                                                                   dict[str, dict[str, TypeNode]]]:
    """The deterministic baseline: types unified across example values (`path` is never inferred)."""
    inputs = infer_fields([e.inputs for e in examples])
    outputs = {x: infer_fields([e.outputs for e in examples if e.exit == x]) for x in exits}
    return inputs, outputs


def schema_from_response(response: Any, exits: Sequence[str]) -> InferredSchema:
    """An `InferSchemaResponse` as fields; type strings the spec parser rejects, and exits other than the declared
    ones, become problems."""
    problems = list(response.problems)
    descriptions: dict[str, str] = {}
    free_text: list[str] = []

    def fields_of(items: Sequence[Any], prefix: str) -> dict[str, TypeNode]:
        out: dict[str, TypeNode] = {}
        for item in items:
            try:
                out[item.name] = parse_type(item.type)
            except ValueError as err:
                problems.append(f"{prefix}.{item.name}: {err}")
                continue
            if item.description:
                descriptions[f"{prefix}.{item.name}"] = item.description
            if item.free_text and prefix != "inputs" and item.name not in free_text:
                free_text.append(item.name)
        return out

    inputs = fields_of(response.inputs, "inputs")
    by_exit = {e.exit: e for e in response.exits}
    outputs = {}
    for exit in exits:
        outputs[exit] = fields_of(by_exit[exit].fields, exit) if exit in by_exit else {}
    extra = [e for e in by_exit if e not in exits]
    if extra:
        problems.append(f"the answer declares exits that the step does not have: {', '.join(extra)}")
    return InferredSchema(inputs, outputs, descriptions, free_text, problems)


def example_problems(examples: Sequence[Example], name: str, inputs: Mapping[str, TypeNode],
                     outputs: Mapping[str, Mapping[str, TypeNode]]) -> list[str]:
    """Plain-language conformance problems of the examples against these fields (spec E-EXAMPLE)."""
    models = build_models(name, inputs, outputs)
    out = []
    for d in check_examples(list(examples), models, list(outputs), None):
        where = ".".join(str(part) for part in d.loc)
        index = d.loc[1] + 1 if len(d.loc) > 1 and isinstance(d.loc[1], int) else None
        prefix = f"example {index} ({where})" if index is not None else where
        out.append(f"{prefix}: {d.message}")
    return out


def infer_interface(
    llm: CompilerLLM,
    *,
    node: str,
    name: str,
    instruction: str,
    exits: Sequence[str],
    examples: Sequence[Example],
    declared_inputs: Mapping[str, TypeNode] | None = None,
    declared_outputs: Mapping[str, Mapping[str, TypeNode]] | None = None,
    constraints: Any = None,
    previous: Interface | None = None,
    guidance: Sequence[str] = (),
) -> InferredSchema:
    """One `infer_schema` call (standard tier); if the examples do not validate against the answer, one re-call with
    the problems appended. A declared half (`declared_inputs`/`declared_outputs`) is kept as given. The result is
    presented, never asked; `problems` (the model's, or problems that remain) make the pipeline ask a
    clarification instead."""
    from wynd.compiler.calls import CALLS
    from wynd.compiler.prompts import render, system_prompt

    base_inputs, base_outputs = baseline_fields(examples, exits)
    sections: list[tuple[str, Any]] = [
        ("Step", {"name": name, "instruction": instruction}),
        ("Exits", list(exits)),
        ("Examples", numbered_examples(examples)),
        ("Baseline", {"inputs": {k: render_type(v) for k, v in base_inputs.items()},
                      "outputs": {x: {k: render_type(v) for k, v in f.items()} for x, f in base_outputs.items()}}),
    ]
    if constraints is not None:
        sections.append(("Constraints from the process graph", asdict(constraints) if is_dataclass(constraints)
                         else constraints))
    given = {}
    if declared_inputs is not None:
        given["inputs"] = {k: render_type(v) for k, v in declared_inputs.items()}
    if declared_outputs is not None:
        given["outputs"] = {x: {k: render_type(v) for k, v in f.items()} for x, f in declared_outputs.items()}
    if given:
        sections.append(("Given schema", given))
    if previous is not None:
        sections.append(("Previous interface", previous.model_dump(mode="json")))
    if guidance:
        sections.append(("User corrections", list(guidance)))

    system = system_prompt("infer_schema")
    result = None
    for attempt in range(2):
        spec = CALLS["infer_schema"]
        response = llm.call("infer_schema", node=node, system=system, prompt=render(sections),
                            response_model=spec.response_model, tier=spec.tier, thinking=spec.thinking).value
        result = schema_from_response(response, exits)
        if declared_inputs is not None:
            result.inputs = dict(declared_inputs)
        if declared_outputs is not None:
            result.outputs = {x: dict(f) for x, f in declared_outputs.items()}
        if result.problems:
            return result
        errors = example_problems(examples, name, result.inputs, result.outputs)
        if not errors:
            return result
        if attempt == 0:
            sections.append(("Problems with your previous answer", errors))
        else:
            result.problems = errors
    return result


def numbered_examples(examples: Sequence[Example]) -> dict[str, dict[str, Any]]:
    """`{"Example 1": {inputs, exit, outputs[, description]}, …}` for prompts (JSON-mode values)."""
    out = {}
    for i, example in enumerate(examples, 1):
        entry: dict[str, Any] = {"inputs": to_jsonable_python(example.inputs), "exit": example.exit}
        if example.exit != RESERVED_EXIT:
            entry["outputs"] = to_jsonable_python(example.outputs)
        if example.description:
            entry["description"] = example.description
        out[f"Example {i}"] = entry
    return out


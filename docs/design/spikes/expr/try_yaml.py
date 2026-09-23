import json
from typing import Literal, Annotated, Union
from pathlib import Path
from datetime import date
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from yamltypes import *

doc = '''kind: proto_step
name: extract_invoice_fields
instruction: |
  Given the text.
inputs:
  invoice_text: string
outputs:
  done:
    invoice_number: string
    total: number
    due_date: date
    lines:
      - sku: string
        qty: integer?
    tags: list[string]?
  not_an_invoice: {}
exits: [done, not_an_invoice]
exit_codes: { 0: done, 1: not_found, "*": error }
flags: [yes, no, on, off, true, False]
examples:
  - inputs: { invoice_text: "..." }
    outputs: { invoice_number: "INV-1042", total: 1200.50, due_date: 2026-10-01 }
    exit: done
'''
data, marks = load_yaml(doc)
for bad in ["a: {x: date?}", "a: {x: list[string]}", "a: [string?]", "a: {x: 'list[string]', y: \"date?\"}"]:
    try: print(repr(bad), "->", load_yaml(bad)[0])
    except YamlError as e: print(repr(bad), "YAML ERR", e)
print(data["flags"], data["exit_codes"], type(data["examples"][0]["outputs"]["due_date"]))
print("line of outputs.done.total:", line_for(marks, ("outputs","done","total")))
print("line of examples[0].outputs.total:", line_for(marks, ("examples",0,"outputs","total")))
print("line for missing path:", line_for(marks, ("examples",0,"outputs","nope", 3)))
try:
    load_yaml("a: 1\nb: 2\na: 3\n")
except YamlError as e: print("dup:", e)
try:
    load_yaml("a: [1, 2\nb: 3\n")
except YamlError as e: print("syntax:", e)

for s in ["string", "list[ integer ]?", "list[list[date?]]", "object?", "strng", "list[string", "list[string]]", "string??"]:
    try: n = parse_type(s); print(s, "->", n, "| render:", render_type(n), "| schema:", json.dumps(to_json_schema(n)))
    except ValueError as e: print(s, "ERR", e)

fields = {k: parse_type(v) for k, v in data["outputs"]["done"].items()}
Done = build_model("ExtractInvoiceFieldsDone", fields, exit="done")
NotInv = build_model("ExtractInvoiceFieldsNotAnInvoice", {}, exit="not_an_invoice")
U = output_union({"done": Done, "not_an_invoice": NotInv})
ta = TypeAdapter(U)
v = ta.validate_python({"exit": "done", "invoice_number": "X", "total": "12.5", "due_date": "2026-10-01", "lines": [{"sku": "a"}]})
print(repr(v))
print(v.model_dump(mode="json"))
try:
    ta.validate_python({"exit": "done", "invoice_number": "X", "total": 1, "due_date": "nope", "lines": [], "extra": 1})
except ValidationError as e:
    for er in e.errors(): print("  loc", er["loc"], er["type"], er["msg"])
try:
    ta.validate_python({"exit": "bogus"})
except ValidationError as e:
    for er in e.errors(): print("  loc", er["loc"], er["type"], er["msg"])
print(json.dumps(Done.model_json_schema())[:600])

# hand-written step style
class Rec(BaseModel):
    total: float
class HDone(BaseModel):
    exit: Literal["done"] = "done"
    record: Rec
    p: Path
    d: date | None = None
class HSpam(BaseModel):
    exit: Literal["spam"] = "spam"
Output = HDone | HSpam
import typing
print(typing.get_origin(Output), typing.get_args(Output))
for m in typing.get_args(Output):
    f = m.model_fields["exit"]; print(m.__name__, typing.get_args(f.annotation), f.default)
print(json.dumps(HDone.model_json_schema()))

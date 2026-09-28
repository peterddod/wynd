import { describe, expect, it } from "vitest";
import type { Json, JsonObject } from "../api/types";
import { fixture } from "../test/fixtures";
import { getIn } from "./json";
import {
  addExit, exits, inputNames, outputFields, outputsForm, removeExit, removeField, renameExit, renameField, setEnv,
  setExitCode, setFieldType, setInstruction,
} from "./protoDoc";

const PROTOS = "processes/process_supplier_invoice/proto";

function proto(name: string): JsonObject {
  return fixture("design").protos[`${PROTOS}/${name}.yaml`]!.doc as JsonObject;
}

describe("outputs form and exits", () => {
  it("detects flat and nested outputs", () => {
    expect(outputsForm(proto("read_pdf"))).toBe("flat");
    expect(outputsForm(proto("extract_invoice_fields"))).toBe("nested");
    expect(outputsForm({ outputs: { done: { a: "string" }, other: {} } })).toBe("nested");
    expect(outputsForm({ outputs: { a: {}, b: {} } })).toBe("flat");       // no `done`: read as flat (ambiguous)
    expect(outputsForm({ exits: ["done"], outputs: {} })).toBe("nested");
  });

  it("lists exits for flat, nested and exits-only docs", () => {
    expect(exits(proto("read_pdf"))).toEqual(["done"]);
    expect(exits(proto("extract_invoice_fields"))).toEqual(["done", "not_an_invoice"]);
    expect(exits({ kind: "proto_step", exits: ["done", "skipped"] })).toEqual(["done", "skipped"]);
    expect(exits({ kind: "proto_step" })).toEqual(["done"]);
    expect(exits({ outputs: { done: {}, missing: {} } })).toEqual(["done", "missing"]);
  });

  it("reads inputs and output fields", () => {
    expect(inputNames(proto("fix_fields"))).toEqual(["invoice_text", "fields", "errors"]);
    expect(outputFields(proto("read_pdf"), "done")).toEqual([["text", "string"], ["pages", "integer"]]);
    expect(outputFields(proto("read_pdf"), "other")).toEqual([]);
    expect(outputFields(proto("extract_invoice_fields"), "not_an_invoice")).toEqual([]);
    expect(outputFields(proto("extract_invoice_fields"), "done").map(([k]) => k)).toContain("due_date");
  });
});

describe("ops", () => {
  it("sets the instruction and field types in both forms", () => {
    expect(getIn(setInstruction(proto("read_pdf"), "Read it."), ["instruction"])).toBe("Read it.");
    expect(getIn(setFieldType(proto("read_pdf"), ["outputs", "done"], "chars", "integer"), ["outputs", "chars"])).toBe("integer");
    expect(getIn(setFieldType(proto("extract_invoice_fields"), ["outputs", "done"], "vat", "number?"), ["outputs", "done", "vat"]))
      .toBe("number?");
    expect(getIn(setFieldType(proto("read_pdf"), ["inputs"], "pdf_path", "string"), ["inputs", "pdf_path"])).toBe("string");
  });

  it("converts flat to nested when an exit is added, keeping the fields", () => {
    const out = addExit(proto("read_pdf"), "encrypted");
    expect(getIn(out, ["outputs"])).toEqual({ done: { text: "string", pages: "integer" }, encrypted: {} });
    expect(getIn(out, ["exits"])).toEqual(["done", "encrypted"]);
    const nested = addExit(proto("extract_invoice_fields"), "duplicate");
    expect(getIn(nested, ["exits"])).toEqual(["done", "not_an_invoice", "duplicate"]);
    expect(getIn(nested, ["outputs", "duplicate"])).toEqual({});
    expect(addExit(nested, "duplicate")).toBe(nested);
  });

  it("renames an exit in outputs, exits and examples", () => {
    const out = renameExit(proto("extract_invoice_fields"), "not_an_invoice", "other_document");
    expect(Object.keys(getIn(out, ["outputs"]) as JsonObject)).toEqual(["done", "other_document"]);
    expect(getIn(out, ["exits"])).toEqual(["done", "other_document"]);
    const examples = getIn(out, ["examples"]) as JsonObject[];
    expect(examples.map((e) => e.exit)).toContain("other_document");
    expect(examples.map((e) => e.exit)).not.toContain("not_an_invoice");
    const flat = renameExit(proto("read_pdf"), "done", "read");
    expect(getIn(flat, ["outputs"])).toEqual({ read: { text: "string", pages: "integer" } });
    expect(getIn(flat, ["exits"])).toEqual(["read"]);
    expect((getIn(flat, ["examples"]) as JsonObject[]).map((e) => e.exit)).toEqual(["read", "read", "error"]);
  });

  it("removes an exit but keeps its examples", () => {
    const doc = proto("extract_invoice_fields");
    const out = removeExit(doc, "not_an_invoice");
    expect(getIn(out, ["exits"])).toEqual(["done"]);
    expect(getIn(out, ["outputs", "not_an_invoice"])).toBeUndefined();
    expect(getIn(out, ["examples"])).toEqual(getIn(doc, ["examples"]));
  });

  it("renames fields in the schema and in the matching examples", () => {
    const inputs = renameField(proto("read_pdf"), ["inputs"], "pdf_path", "file");
    expect(getIn(inputs, ["inputs"])).toEqual({ file: "path" });
    expect((getIn(inputs, ["examples"]) as JsonObject[]).map((e) => Object.keys(e.inputs as JsonObject))).toEqual([["file"], ["file"], ["file"]]);
    const doc = proto("extract_invoice_fields");
    const outputs = renameField(doc, ["outputs", "done"], "total", "amount");
    expect(Object.keys(getIn(outputs, ["outputs", "done"]) as JsonObject)).toEqual(["supplier", "invoice_number", "amount", "currency", "due_date"]);
    const examples = getIn(outputs, ["examples"]) as JsonObject[];
    for (const ex of examples) {
      if (ex.exit === "done") expect(Object.keys(ex.outputs as JsonObject)).toContain("amount");
    }
  });

  it("removes a field from the schema only", () => {
    const doc = proto("read_pdf");
    const out = removeField(doc, ["outputs", "done"], "pages");
    expect(getIn(out, ["outputs"])).toEqual({ text: "string" });
    expect(getIn(out, ["examples", 0, "outputs", "pages"])).toBe(1);
  });

  it("maps exit codes with string keys and drops an emptied mapping", () => {
    const doc: Json = { kind: "proto_step" };
    const one = setExitCode(doc, "0", "done");
    expect(getIn(one, ["exit_codes"])).toEqual({ "0": "done" });
    const two = setExitCode(one, "*", "error");
    expect(Object.keys(getIn(two, ["exit_codes"]) as JsonObject)).toEqual(["0", "*"]);
    expect(getIn(setExitCode(one, "0", undefined), ["exit_codes"])).toBeUndefined();
  });

  it("sets env deps and requires", () => {
    const doc = proto("read_pdf");
    expect(getIn(setEnv(doc, { deps: ["pypdf>=6,<7"] }), ["env", "deps"])).toEqual(["pypdf>=6,<7"]);
    expect(getIn(setEnv(doc, { requires: "glibc" }), ["env", "requires"])).toBe("glibc");
    expect(getIn(setEnv({ env: { requires: "glibc" } }, { requires: null }), ["env"])).toBeUndefined();
  });
});

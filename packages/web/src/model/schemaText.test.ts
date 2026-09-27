import { describe, expect, it } from "vitest";
import type { Interface, JsonSchema } from "../api/types";
import { fixture } from "../test/fixtures";
import { describe as say, describeFields, schemaFields, stepSentence } from "./schemaText";

describe("describe", () => {
  const rows: [JsonSchema, string][] = [
    [{ enum: ["a", "b"] }, 'one of "a", "b"'],
    [{ anyOf: [{ type: "string" }, { type: "null" }] }, "text, or empty"],
    [{ anyOf: [{ type: "integer" }, { type: "string", format: "date" }] }, "a whole number or a date"],
    [{ type: ["number", "null"] }, "a number, or empty"],
    [{ type: "string", format: "path" }, "a file path"],
    [{ type: "string", format: "date" }, "a date"],
    [{ type: "string", format: "date-time" }, "a date and time"],
    [{ type: "string" }, "text"],
    [{ type: "number" }, "a number"],
    [{ type: "integer" }, "a whole number"],
    [{ type: "boolean" }, "yes or no"],
    [{ type: "null" }, "nothing"],
    [{ type: "array" }, "a list"],
    [{ type: "array", items: { type: "string" } }, "a list of text values"],
    [{ type: "array", items: { type: "number" } }, "a list of numbers"],
    [{ type: "array", items: { type: "string", format: "date" } }, "a list of dates"],
    [{ type: "array", items: { type: "string", format: "path" } }, "a list of file paths"],
    [{ type: "array", items: { type: "boolean" } }, "a list of yes/no values"],
    [{ type: "array", items: { type: "array", items: { type: "integer" } } }, "a list of lists of whole numbers"],
    [{ type: "object" }, "a record (any fields)"],
    [{ type: "object", properties: { a: { type: "string" } }, required: ["a"] }, "a record with a (text)"],
    [{}, "any value"],
  ];
  for (const [schema, text] of rows) it(`${JSON.stringify(schema)} -> ${text}`, () => expect(say(schema)).toBe(text));

  it("describes arrays of records with their fields", () => {
    const s: JsonSchema = {
      type: "array",
      items: { type: "object", properties: { field: { type: "string" }, fixable: { type: "boolean" } }, required: ["field"] },
    };
    expect(say(s)).toBe("a list of records, each with field (text), fixable (yes or no, optional)");
  });

  it("resolves $refs into $defs", () => {
    const s: JsonSchema = {
      $defs: { Err: { type: "object", properties: { code: { type: "string" } }, required: ["code"] } },
      type: "object",
      properties: { errors: { type: "array", items: { $ref: "#/$defs/Err" } }, first: { allOf: [{ $ref: "#/$defs/Err" }] } },
      required: ["errors"],
    };
    expect(describeFields(s)).toBe("errors (a list of records, each with code (text)), first (a record with code (text), optional)");
  });
});

describe("describeFields and stepSentence", () => {
  it("marks optional fields and skips the exit discriminator", () => {
    const s: JsonSchema = {
      type: "object",
      properties: { exit: { const: "done", type: "string" }, a: { type: "string" }, b: { type: "number" } },
      required: ["a"],
    };
    expect(describeFields(s)).toBe("a (text), b (a number, optional)");
    expect(schemaFields(s).map((f) => f.name)).toEqual(["a", "b"]);
  });

  it("says what the extract step does from the fixture interface", () => {
    const iface = fixture("design").steps.extract!.interface;
    expect(stepSentence(iface)).toBe(
      "Takes invoice_text (text). Finishes with done, returning supplier (text), invoice_number (text), total (a number), "
      + "currency (text) and due_date (a date); or not_an_invoice, returning nothing.");
  });

  it("joins three exits and handles no inputs", () => {
    const iface: Interface = {
      inputs: { type: "object", properties: {} },
      exits: [
        { name: "a", schema: null },
        { name: "b", schema: { type: "object", properties: { x: { type: "integer" } }, required: ["x"] } },
        { name: "c", schema: null },
      ],
      source: "declared",
    };
    expect(stepSentence(iface)).toBe("Takes nothing. Finishes with a, returning nothing; b, returning x (a whole number); or c, returning nothing.");
  });

  it("describes the fix step's referenced inputs", () => {
    const text = stepSentence(fixture("design").steps.fix!.interface);
    expect(text).toContain("fields (a record with supplier (text), invoice_number (text), total (a number), currency (text), due_date (a date))");
    expect(text).toContain("errors (a list of records, each with field (text)");
  });
});

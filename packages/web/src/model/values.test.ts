import { describe, expect, it } from "vitest";
import type { Interface } from "../api/types";
import { fixture } from "../test/fixtures";
import { exampleSentence, formatLoose, parseLoose, parseTyped } from "./values";

describe("parseLoose / formatLoose", () => {
  it("parses JSON and keeps anything else as text", () => {
    expect(parseLoose("123")).toBe(123);
    expect(parseLoose("abc")).toBe("abc");
    expect(parseLoose('{"a":1}')).toEqual({ a: 1 });
    expect(parseLoose("true")).toBe(true);
    expect(parseLoose("null")).toBeNull();
    expect(parseLoose('"quoted"')).toBe("quoted");
    expect(parseLoose("")).toBe("");
  });

  it("formats strings raw and everything else as JSON", () => {
    expect(formatLoose("abc")).toBe("abc");
    expect(formatLoose(12.5)).toBe("12.5");
    expect(formatLoose({ a: [1] })).toBe('{"a":[1]}');
    expect(formatLoose(null)).toBe("null");
  });
});

describe("parseTyped", () => {
  it("parses numbers and integers", () => {
    expect(parseTyped("1200.5", { type: "number" })).toEqual({ ok: true, value: 1200.5 });
    expect(parseTyped("abc", { type: "number" })).toEqual({ ok: false, error: "expected a number" });
    expect(parseTyped("3", { type: "integer" })).toEqual({ ok: true, value: 3 });
    expect(parseTyped("3.5", { type: "integer" })).toEqual({ ok: false, error: "expected a whole number" });
    expect(parseTyped("", { type: "number" }).ok).toBe(false);
  });

  it("checks dates and keeps other strings as typed", () => {
    expect(parseTyped("2026-10-01", { type: "string", format: "date" })).toEqual({ ok: true, value: "2026-10-01" });
    expect(parseTyped("1 Oct", { type: "string", format: "date" })).toEqual({ ok: false, error: "expected a date (YYYY-MM-DD)" });
    expect(parseTyped(" x ", { type: "string" })).toEqual({ ok: true, value: " x " });
  });

  it("parses booleans, nullables and JSON", () => {
    expect(parseTyped("false", { type: "boolean" })).toEqual({ ok: true, value: false });
    expect(parseTyped("", { anyOf: [{ type: "number" }, { type: "null" }] })).toEqual({ ok: true, value: null });
    expect(parseTyped("4", { anyOf: [{ type: "number" }, { type: "null" }] })).toEqual({ ok: true, value: 4 });
    expect(parseTyped('{"a": 1}', { type: "object" })).toEqual({ ok: true, value: { a: 1 } });
    expect(parseTyped("[1]", { type: "object" })).toEqual({ ok: false, error: "expected a JSON object" });
    const bad = parseTyped("{a: 1}", { type: "object" });
    expect(bad.ok).toBe(false);
    expect(!bad.ok && bad.error).toMatch(/^invalid JSON/);
    expect(parseTyped("oops", null)).toEqual({ ok: true, value: "oops" });
  });
});

describe("exampleSentence", () => {
  const extract = (): Interface => fixture("design").steps.extract!.interface;

  it("says what goes in and what comes out, in schema order", () => {
    const ex = { inputs: { invoice_text: "Short" }, outputs: { total: 1200.5, invoice_number: "INV-1042" }, exit: "done" };
    expect(exampleSentence(ex, extract())).toBe('Given invoice_text = "Short" → done with invoice_number = "INV-1042", total = 1200.5');
  });

  it("truncates long values and handles examples without outputs or inputs", () => {
    const ex = { inputs: { invoice_text: "Dear customer, your order has shipped" }, exit: "not_an_invoice" };
    expect(exampleSentence(ex, extract())).toBe('Given invoice_text = "Dear customer, your or…" → not_an_invoice');
    expect(exampleSentence({ exit: "error" }, null)).toBe("Given no inputs → error");
    expect(exampleSentence({ inputs: { n: 1 } }, null)).toBe("Given n = 1 → done");
  });
});

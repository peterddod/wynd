import { describe, expect, it } from "vitest";
import { completionPrefix, lex, renameStepInExpr } from "./expr";

describe("lex", () => {
  it("lexes the spec's if/elif/else example", () => {
    const expr = 'if steps.classify.outputs.kind == "invoice" then env.INVOICE_DIR elif steps.x.runs < 3.5 then \'r\' else env.MISC_DIR';
    const toks = lex(expr)!;
    expect(toks.map((t) => t.s).join("")).toBe(expr);
    const meaningful = toks.filter((t) => t.t !== "ws");
    expect(meaningful.slice(0, 8).map((t) => [t.t, t.s])).toEqual([
      ["id", "if"], ["id", "steps"], ["punct", "."], ["id", "classify"], ["punct", "."], ["id", "outputs"],
      ["punct", "."], ["id", "kind"],
    ]);
    expect(meaningful.find((t) => t.t === "str")).toMatchObject({ s: '"invoice"', quote: '"' });
    expect(meaningful.find((t) => t.t === "num")).toMatchObject({ s: "3.5" });
    expect(meaningful.find((t) => t.quote === "'")).toMatchObject({ s: "'r'" });
    for (const t of toks) expect(expr.slice(t.start, t.end)).toBe(t.s);
  });

  it("returns null on an unterminated string and handles escapes", () => {
    expect(lex('"open')).toBeNull();
    expect(lex("'it\\'s'")?.map((t) => t.t)).toEqual(["str"]);
  });
});

describe("renameStepInExpr", () => {
  const cases: [string, string][] = [
    ["steps.a.outputs.x", "steps.b.outputs.x"],
    ["steps . a . exit", "steps . b . exit"],
    ["x.steps.a", "x.steps.a"],
    ['"steps.a"', '"steps.a"'],
    ['edges["a.done"][0].taken', 'edges["b.done"][0].taken'],
    ["edges['a.done'].retry.taken", "edges['b.done'].retry.taken"],
    ["steps.ab.outputs", "steps.ab.outputs"],
    ['edges["ab.done"][0].taken', 'edges["ab.done"][0].taken'],
    ["steps.a.runs < 3 and steps.c.outputs.a", "steps.b.runs < 3 and steps.c.outputs.a"],
  ];
  for (const [input, want] of cases) {
    it(`${input} -> ${want}`, () => expect(renameStepInExpr(input, "a", "b")).toBe(want));
  }

  it("is byte-identical when nothing matches and null when lexing fails", () => {
    const text = "  default( steps.c.outputs.x ,\n 'a' )  ";
    expect(renameStepInExpr(text, "a", "b")).toBe(text);
    expect(renameStepInExpr('steps.a == "x', "a", "b")).toBeNull();
  });
});

describe("completionPrefix", () => {
  it("finds the reference before the caret, mid-token too", () => {
    expect(completionPrefix("steps.validate.outputs.va", 25)).toEqual({ start: 0, prefix: "steps.validate.outputs.va" });
    expect(completionPrefix("steps.validate.outputs.valid", 17)).toEqual({ start: 0, prefix: "steps.validate.ou" });
    expect(completionPrefix("x > 3 and steps.", 16)).toEqual({ start: 10, prefix: "steps." });
    expect(completionPrefix('edges["x.y"].', 13)).toEqual({ start: 0, prefix: 'edges["x.y"].' });
    expect(completionPrefix("edges[\"x.y\"][0].ta", 18)).toEqual({ start: 0, prefix: 'edges["x.y"][0].ta' });
  });

  it("returns null inside a string literal or where no reference is typed", () => {
    expect(completionPrefix('steps.a == "ste', 15)).toBeNull();
    expect(completionPrefix("3 + ", 4)).toBeNull();
    expect(completionPrefix("", 0)).toBeNull();
  });
});

import { describe, expect, it } from "vitest";
import type { Json } from "../api/types";
import { fixture } from "../test/fixtures";
import { branchesOnCycles } from "./cycles";

describe("branchesOnCycles", () => {
  it("finds exactly validate.done[1] and fix.done[0] in the dogfood", () => {
    expect(branchesOnCycles(fixture("design").process_file.doc)).toEqual(new Set(["3:1", "4:0"]));
  });

  it("counts a self-loop and never an exit target", () => {
    const doc: Json = {
      edges: [
        { from: "a.done", to: [{ step: "a", when: "steps.a.runs < 3" }, { step: "$exit.done" }] },
        { from: "a.error", to: "$ignore" },
      ],
    };
    expect(branchesOnCycles(doc)).toEqual(new Set(["0:0"]));
  });

  it("marks every branch of a longer cycle and none outside it", () => {
    const doc = {
      edges: [
        { from: "a.done", to: "b" },
        { from: "b.done", to: [{ step: "c", when: "x" }, "d"] },
        { from: "c.done", to: "a" },
        { from: "d.done", to: "$exit.done" },
      ],
    };
    expect(branchesOnCycles(doc)).toEqual(new Set(["0:0", "1:0", "2:0"]));
  });

  it("ignores branches after the else", () => {
    const doc = { edges: [{ from: "a.done", to: ["b", "a"] }] };
    expect(branchesOnCycles(doc)).toEqual(new Set());
  });
});

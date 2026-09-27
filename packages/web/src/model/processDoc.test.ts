import { describe, expect, it } from "vitest";
import type { Json, JsonObject } from "../api/types";
import { fixture } from "../test/fixtures";
import { deepEqual, getIn } from "./json";
import {
  addBranch, addStep, branchLoc, edgeIndexFor, edgesOf, expandShorthand, moveBranch, parseFrom, parseTarget,
  removeBranch, removeStep, renameBranchMapKey, renameProcessExit, renameStep, routeExit, setBase, setBranchMapValue,
  setEdgeKind, setFieldMap, setFinally, setTop, stepNames, targetString, updateBranch,
} from "./processDoc";

function dogfood(): JsonObject {
  return fixture("design").process_file.doc as JsonObject;
}

/** The dogfood plus keys the UI does not know about, on the doc, an edge and a branch. */
function withUnknownKeys(): JsonObject {
  const doc = dogfood();
  doc.x_owner = { team: "finance" };
  const edges = doc.edges as JsonObject[];
  (edges[0] as JsonObject).x_note = "shorthand edge note";
  ((edges[3] as JsonObject).to as JsonObject[])[0]!.x_label = "happy path";
  return doc;
}

const keys = (v: Json | undefined): string[] => Object.keys(v as JsonObject);

describe("read views", () => {
  it("parses from and targets", () => {
    expect(parseFrom("validate.done")).toEqual({ step: "validate", exit: "done" });
    expect(parseTarget("$exit.done")).toEqual({ type: "exit", exit: "done" });
    expect(parseTarget("$ignore")).toEqual({ type: "ignore" });
    expect(parseTarget("save")).toEqual({ type: "step", step: "save" });
    for (const t of ["$exit.done", "$ignore", "save"]) expect(targetString(parseTarget(t))).toBe(t);
  });

  it("reads the dogfood edges", () => {
    const edges = edgesOf(dogfood());
    expect(edges).toHaveLength(7);
    expect(stepNames(dogfood())).toEqual(["read", "extract", "validate", "fix", "save", "escalate"]);
    const validate = edges[3]!;
    expect(validate.from).toBe("validate.done");
    expect(validate.shorthand).toBe(false);
    expect(validate.branches).toHaveLength(3);
    expect(validate.branches.map((b) => b.isElse)).toEqual([false, false, true]);
    expect(validate.branches[1]!.when).toBe("steps.validate.outputs.fixable and steps.fix.runs < 3");
    expect(validate.branches[2]!.target).toEqual({ type: "step", step: "escalate" });
    expect(edges[2]!.branches[0]!.target).toEqual({ type: "exit", exit: "not_an_invoice" });
    expect(edgeIndexFor(dogfood(), "fix", "done")).toBe(4);
    expect(edgeIndexFor(dogfood(), "fix", "error")).toBe(-1);
  });

  it("maps a shorthand edge's with to the edge through branchLoc", () => {
    const doc = dogfood();
    const read = edgesOf(doc)[0]!;
    expect(read.shorthand).toBe(true);
    expect(read.branches[0]!.with).toEqual({ invoice_text: "steps.read.outputs.text" });
    expect(branchLoc(read, 0, "with", "invoice_text")).toEqual(["edges", 0, "with", "invoice_text"]);
    expect(branchLoc(read, 0, "step")).toEqual(["edges", 0, "to"]);
    expect(getIn(doc, branchLoc(read, 0, "with", "invoice_text"))).toBe("steps.read.outputs.text");
    expect(branchLoc(edgesOf(doc)[3]!, 1, "when")).toEqual(["edges", 3, "to", 1, "when"]);
  });

  it("marks branches after the else as ignored, reads string items, $ignore, check and extra keys", () => {
    const doc: Json = {
      edges: [
        { from: "a.done", to: ["b", { step: "c", when: "x" }, { step: "$ignore" }], kind: "agentic" },
        { from: "b.done", to: [{ step: "c", check: "is it an invoice?", context: ["previous.outputs"], x_y: 1 }, "d"] },
        { from: "c.done", to: "$ignore", x_note: "n" },
      ],
    };
    const [a, b, c] = edgesOf(doc);
    expect(a!.kind).toBe("agentic");
    expect(a!.branches.map((x) => [x.isElse, x.ignored])).toEqual([[true, false], [false, true], [true, true]]);
    expect(b!.branches[0]).toMatchObject({ isElse: false, check: "is it an invoice?", context: ["previous.outputs"], extra: { x_y: 1 } });
    expect(b!.branches[1]!.isElse).toBe(true);
    expect(c!.branches[0]!.target).toEqual({ type: "ignore" });
    expect(c!.branches[0]!.extra).toEqual({ x_note: "n" });
  });
});

describe("routing ops", () => {
  it("routes an unrouted exit as a shorthand edge", () => {
    const doc = dogfood();
    const r = routeExit(doc, "extract", "error", { type: "exit", exit: "needs_review" });
    expect(r.edgeIndex).toBe(7);
    expect(r.branchIndex).toBe(0);
    expect(getIn(r.doc, ["edges", 7])).toEqual({ from: "extract.error", to: "$exit.needs_review" });
    expect(getIn(doc, ["edges"])).toHaveLength(7);
  });

  it("inserts a new branch before an existing else with an empty condition", () => {
    const r = routeExit(dogfood(), "validate", "done", { type: "step", step: "save" });
    expect(r).toMatchObject({ edgeIndex: 3, branchIndex: 2 });
    const to = getIn(r.doc, ["edges", 3, "to"]) as JsonObject[];
    expect(to.map((b) => b.step)).toEqual(["save", "fix", "save", "escalate"]);
    expect(to[2]).toEqual({ step: "save", when: "" });
  });

  it("appends a branch as the else when the edge has none", () => {
    const doc = dogfood();
    const cond = updateBranch(doc, 0, 0, { when: "steps.read.outputs.pages > 0" });
    const r = addBranch(cond, 0, { type: "exit", exit: "not_an_invoice" });
    expect(r.branchIndex).toBe(1);
    expect(getIn(r.doc, ["edges", 0, "to"])).toEqual([
      { step: "extract", when: "steps.read.outputs.pages > 0", with: { invoice_text: "steps.read.outputs.text" } },
      { step: "$exit.not_an_invoice" },
    ]);
  });

  it("expands shorthand keeping kind and unknown keys on the edge", () => {
    const doc = { edges: [{ from: "a.done", kind: "agentic", to: "b", with: { x: "1" }, limits: { timeout: 5 }, x_note: "n" }] };
    const out = expandShorthand(doc, 0);
    expect(getIn(out, ["edges", 0])).toEqual({
      from: "a.done", kind: "agentic", to: [{ step: "b", with: { x: "1" }, limits: { timeout: 5 } }], x_note: "n",
    });
    expect(keys(getIn(out, ["edges", 0]))).toEqual(["from", "kind", "to", "x_note"]);
    expect(expandShorthand(out, 0)).toBe(out);
  });

  it("updates, names and retargets branches", () => {
    const doc = dogfood();
    const named = updateBranch(doc, 3, 1, { name: "retry" });
    expect(keys(getIn(named, ["edges", 3, "to", 1]))).toEqual(["step", "name", "when", "with"]);
    expect(updateBranch(named, 3, 1, { name: null })).toEqual(doc);
    const retargeted = updateBranch(doc, 3, 2, { target: { type: "exit", exit: "needs_review" } });
    expect(getIn(retargeted, ["edges", 3, "to", 2, "step"])).toBe("$exit.needs_review");
    const short = updateBranch(doc, 5, 0, { target: { type: "step", step: "escalate" } });
    expect(getIn(short, ["edges", 5, "to"])).toBe("escalate");
    const checked = updateBranch(doc, 5, 0, { check: "the record looks complete" });
    expect(getIn(checked, ["edges", 5, "check"])).toBe("the record looks complete");
    const pending = updateBranch(doc, 3, 2, { when: "" });
    expect(getIn(pending, ["edges", 3, "to", 2, "when"])).toBe("");
  });

  it("writes and deletes with/limits values, deleting an emptied map", () => {
    const doc = dogfood();
    const short = setBranchMapValue(doc, 0, 0, "with", "invoice_text", undefined);
    expect(getIn(short, ["edges", 0])).toEqual({ from: "read.done", to: "extract" });
    const limited = setBranchMapValue(doc, 3, 1, "limits", "max_traversals", 5);
    expect(getIn(limited, ["edges", 3, "to", 1, "limits"])).toEqual({ max_traversals: 5 });
    const renamed = renameBranchMapKey(doc, 3, 0, "with", "dest", "destination");
    expect(keys(getIn(renamed, ["edges", 3, "to", 0, "with"]))).toEqual(["record", "destination"]);
    const strings = { edges: [{ from: "a.done", to: ["b", "c"] }] };
    expect(getIn(setBranchMapValue(strings, 0, 1, "with", "x", "1"), ["edges", 0, "to"])).toEqual(["b", { step: "c", with: { x: "1" } }]);
  });

  it("moves and removes branches; the last branch removes the edge", () => {
    const doc = dogfood();
    const moved = moveBranch(doc, 3, 2, 0);
    expect((getIn(moved, ["edges", 3, "to"]) as JsonObject[]).map((b) => b.step)).toEqual(["escalate", "save", "fix"]);
    const removed = removeBranch(doc, 3, 1);
    expect((getIn(removed, ["edges", 3, "to"]) as JsonObject[]).map((b) => b.step)).toEqual(["save", "escalate"]);
    const gone = removeBranch(doc, 2, 0);
    expect(edgesOf(gone).map((e) => e.from)).not.toContain("extract.not_an_invoice");
    expect(setEdgeKind(doc, 3, "agentic")).not.toBe(doc);
    expect(getIn(setEdgeKind(setEdgeKind(doc, 3, "agentic"), 3, "deterministic"), ["edges", 3])).toEqual(getIn(doc, ["edges", 3]));
  });
});

describe("step ops", () => {
  it("adds a step and sets the entry only when there is none", () => {
    const doc = addStep(dogfood(), "notify", "./steps/notify");
    expect(getIn(doc, ["steps", "notify"])).toEqual({ use: "./steps/notify" });
    expect(getIn(doc, ["entry"])).toBe("read");
    const fresh = addStep({ kind: "process", name: "p" }, "first", "./steps/first");
    expect(fresh).toEqual({ kind: "process", name: "p", steps: { first: { use: "./steps/first" } }, entry: "first" });
  });

  it("removes a step, its edges and the branches targeting it", () => {
    const doc = { ...dogfood(), on_error: "fix", finally: ["fix", "save"] };
    const out = removeStep(doc, "fix");
    expect(stepNames(out)).not.toContain("fix");
    const edges = edgesOf(out);
    expect(edges.map((e) => e.from)).not.toContain("fix.done");
    const validate = edges.find((e) => e.from === "validate.done")!;
    expect(validate.branches.map((b) => b.target)).toEqual([{ type: "step", step: "save" }, { type: "step", step: "escalate" }]);
    expect(getIn(out, ["on_error"])).toBeUndefined();
    expect(getIn(out, ["finally"])).toEqual(["save"]);
    expect(getIn(removeStep(dogfood(), "read"), ["entry"])).toBeUndefined();
    const onlyTarget = removeStep(dogfood(), "extract");
    expect(edgesOf(onlyTarget).map((e) => e.from)).toEqual(["validate.done", "fix.done", "save.done", "escalate.done"]);
  });

  it("renames a step everywhere, leaving string literals and member chains alone", () => {
    const doc = dogfood();
    ((doc.edges as JsonObject[])[3]!.to as JsonObject[])[0]!.when =
      'steps.validate.outputs.valid and edges["validate.done"][1].taken < 2 and "steps.validate" != x.steps.validate';
    const { doc: out, rewritten, unparsed } = renameStep(doc, "validate", "check");
    expect(stepNames(out)).toEqual(["read", "extract", "check", "fix", "save", "escalate"]);
    const edges = edgesOf(out);
    expect(edges[1]!.branches[0]!.target).toEqual({ type: "step", step: "check" });
    expect(edges[3]!.from).toBe("check.done");
    expect(edges[4]!.branches[0]!.target).toEqual({ type: "step", step: "check" });
    expect(edges[3]!.branches[0]!.when).toBe(
      'steps.check.outputs.valid and edges["check.done"][1].taken < 2 and "steps.validate" != x.steps.validate');
    expect(edges[3]!.branches[0]!.with.dest).toBe("if steps.check.outputs.fields.total > 10000 then env.REVIEW_DIR else env.RECORDS_DIR");
    expect(edges[3]!.branches[2]!.with.errors).toBe("steps.check.outputs.errors");
    // save.when (1) + save.with (2) + fix.when (1) + fix.with (2) + escalate.with (2)
    expect(rewritten).toBe(8);
    expect(unparsed).toEqual([]);
    const reported = renameStep({ steps: { a: { use: "x" } }, edges: [{ from: "b.done", to: "a", with: { v: '"open' } }] }, "a", "z");
    expect(reported.unparsed).toEqual([["edges", 0, "with", "v"]]);
    expect(getIn(renameStep(dogfood(), "read", "load").doc, ["entry"])).toBe("load");
  });

  it("renames a process exit in outputs, targets and examples", () => {
    const out = renameProcessExit(dogfood(), "needs_review", "escalated");
    expect(keys(getIn(out, ["outputs"]))).toEqual(["done", "not_an_invoice", "escalated"]);
    expect(getIn(out, ["edges", 6, "to"])).toBe("$exit.escalated");
    expect(getIn(out, ["examples", 4, "exit"])).toBe("escalated");
    expect(getIn(out, ["examples", 0, "exit"])).toBe("done");
  });
});

describe("top-level ops", () => {
  it("sets and deletes top-level keys and env.base", () => {
    const doc = dogfood();
    expect(getIn(setTop(doc, "latency", "fast"), ["latency"])).toBe("fast");
    expect(getIn(setTop(doc, "goal", undefined), ["goal"])).toBeUndefined();
    expect(getIn(setBase(doc, "alpine-python"), ["env"])).toMatchObject({ base: "alpine-python" });
    expect(getIn(setBase({ env: { base: "alpine-python" } }, undefined), ["env"])).toBeUndefined();
    expect(getIn(setFinally(doc, []), ["finally"])).toBeUndefined();
    const fin = setFinally({ finally: [{ step: "a", with: { x: "1" } }] }, ["b", "a"]);
    expect(getIn(fin, ["finally"])).toEqual(["b", { step: "a", with: { x: "1" } }]);
    expect(getIn(setFieldMap(doc, ["outputs", "needs_review"], [["ticket", "path"]]), ["outputs", "needs_review"])).toEqual({ ticket: "path" });
    expect(getIn(setFieldMap({ outputs: { text: "string" } }, ["outputs", "done"], [["body", "string"]]), ["outputs"])).toEqual({ body: "string" });
  });
});

describe("losslessness", () => {
  it("keeps unknown keys and key order through every op", () => {
    const doc = withUnknownKeys();
    const ops: ((d: Json) => Json)[] = [
      (d) => addStep(d, "notify", "./steps/notify"),
      (d) => removeStep(d, "escalate"),
      (d) => renameStep(d, "fix", "repair").doc,
      (d) => routeExit(d, "validate", "done", { type: "step", step: "fix" }).doc,
      (d) => updateBranch(d, 3, 0, { when: "true" }),
      (d) => setBranchMapValue(d, 3, 0, "limits", "timeout", 30),
      (d) => moveBranch(d, 3, 0, 1),
      (d) => setTop(d, "latency", "normal"),
      (d) => renameProcessExit(d, "done", "saved"),
    ];
    for (const op of ops) {
      const out = op(doc) as JsonObject;
      expect(out.x_owner).toEqual({ team: "finance" });
      expect(keys(out).filter((k) => k in doc)).toEqual(keys(doc).filter((k) => k in out));
      expect(getIn(out, ["edges", 0, "x_note"])).toBe("shorthand edge note");
    }
    expect(getIn(updateBranch(doc, 3, 0, { when: "x" }), ["edges", 3, "to", 0, "x_label"])).toBe("happy path");
  });

  it("returns a deep-equal doc with the original key order after a when edit is undone", () => {
    const doc = dogfood();
    const edited = updateBranch(doc, 3, 1, { when: "steps.validate.outputs.fixable" });
    const undone = updateBranch(edited, 3, 1, { when: "steps.validate.outputs.fixable and steps.fix.runs < 3" });
    expect(deepEqual(undone, doc)).toBe(true);
    expect(JSON.stringify(undone)).toBe(JSON.stringify(doc));
    expect(edited).not.toBe(doc);
    expect(getIn(edited, ["edges", 0])).toBe(getIn(doc, ["edges", 0]));
  });

  it("returns the same doc when an op changes nothing", () => {
    const doc = dogfood();
    expect(setTop(doc, "goal", getIn(doc, ["goal"]))).toBe(doc);
    expect(setBranchMapValue(doc, 3, 0, "with", "missing", undefined)).toBe(doc);
    expect(renameStep(doc, "nope", "other").doc).toBe(doc);
  });
});

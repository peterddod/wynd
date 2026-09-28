import { describe, expect, it } from "vitest";
import type { Issue, JsonObject } from "../api/types";
import { fixture } from "../test/fixtures";
import { type BranchEdgeData, type FlowInput, type StepNodeData, type TerminalNodeData, structureKey, toFlow } from "./graph";
import { setIn } from "./json";

function input(over: Partial<FlowInput> = {}): FlowInput {
  const design = fixture("design");
  return { process: design.process_file.doc, protos: {}, steps: design.steps, issues: design.validation.issues, readOnly: false, ...over };
}

function stepData(flow: ReturnType<typeof toFlow>, name: string): StepNodeData {
  return flow.nodes.find((n) => n.id === `s:${name}`)!.data as StepNodeData;
}

function edgeData(flow: ReturnType<typeof toFlow>, id: string): BranchEdgeData {
  return flow.edges.find((e) => e.id === id)!.data as BranchEdgeData;
}

describe("toFlow on the dogfood", () => {
  const flow = toFlow(input());

  it("has 6 steps, the inputs node and 3 terminals", () => {
    expect(flow.nodes.map((n) => n.id)).toEqual([
      "in", "s:read", "s:extract", "s:validate", "s:fix", "s:save", "s:escalate", "x:done", "x:not_an_invoice", "x:needs_review",
    ]);
    expect(flow.nodes.find((n) => n.id === "in")!.data).toEqual({ kind: "inputs", fields: ["pdf_path"] });
  });

  it("has 9 branch edges plus the entry edge", () => {
    const branches = flow.edges.filter((e) => e.type === "branch");
    expect(branches).toHaveLength(9);
    const entry = flow.edges.find((e) => e.id === "entry")!;
    expect(entry).toMatchObject({ source: "in", target: "s:read", selectable: false, deletable: false });
    expect(flow.edges.find((e) => e.id === "b:3:1")).toMatchObject({
      source: "s:validate", sourceHandle: "o:done", target: "s:fix", targetHandle: "t",
    });
    expect(flow.edges.find((e) => e.id === "b:2:0")!.target).toBe("x:not_an_invoice");
  });

  it("gives extract its declared exits plus the implicit error", () => {
    expect(stepData(flow, "extract").exits).toEqual([
      { name: "done", routed: true, implicit: false, declared: true },
      { name: "not_an_invoice", routed: true, implicit: false, declared: true },
      { name: "error", routed: false, implicit: true, declared: true },
    ]);
    const node = flow.nodes.find((n) => n.id === "s:extract")!;
    expect(node.ariaLabel).toBe("Step extract, agentic, compiled, exits: done, not_an_invoice, error");
    expect(stepData(flow, "read")).toMatchObject({ isEntry: true, stepKind: "deterministic", phase: "compiled", use: "./steps/read_pdf" });
    expect(stepData(flow, "fix")).toMatchObject({ phase: "design", tier: "cheap" });
  });

  it("labels the validate.done branches and marks the cycle branches", () => {
    expect(edgeData(flow, "b:3:0").label).toBe("1. if steps.validate.outputs.valid");
    expect(edgeData(flow, "b:3:1").label).toBe("2. if steps.validate.outputs.fixable a…");
    expect(edgeData(flow, "b:3:2")).toMatchObject({ label: "3. else", isElse: true, ignored: false });
    expect(edgeData(flow, "b:0:0").label).toBe("");
    expect(edgeData(flow, "b:3:1")).toMatchObject({ onCycle: true, maxTraversals: null, hasIssues: false });
    expect(edgeData(flow, "b:4:0").onCycle).toBe(true);
    expect(edgeData(flow, "b:3:0").onCycle).toBe(false);
  });
});

describe("toFlow edge cases", () => {
  it("marks a branch after the else as ignored and names named branches", () => {
    const design = fixture("design");
    const doc = setIn(design.process_file.doc, ["edges", 3, "to", 3], { step: "save", name: "late" });
    const flow = toFlow(input({ process: doc }));
    expect(edgeData(flow, "b:3:3")).toMatchObject({ ignored: true, label: "4. else [late]" });
  });

  it("adds a declared:false terminal for an undeclared $exit target and one $ignore terminal", () => {
    let doc = setIn(fixture("design").process_file.doc, ["edges", 7], { from: "extract.error", to: "$exit.foo" });
    doc = setIn(doc, ["edges", 8], { from: "save.error", to: "$ignore" });
    doc = setIn(doc, ["edges", 9], { from: "escalate.error", to: "$ignore" });
    const flow = toFlow(input({ process: doc }));
    const foo = flow.nodes.find((n) => n.id === "x:foo")!;
    expect(foo.data as TerminalNodeData).toEqual({ kind: "exit", exit: "foo", declared: false });
    expect(flow.nodes.filter((n) => n.id === "x:$ignore")).toHaveLength(1);
    expect(flow.edges.filter((e) => e.target === "x:$ignore")).toHaveLength(2);
    expect(stepData(flow, "extract").exits.find((x) => x.name === "error")!.routed).toBe(true);
  });

  it("shows an exit used by an edge but not declared by the step", () => {
    const doc = setIn(fixture("design").process_file.doc, ["edges", 7], { from: "read.encrypted", to: "escalate" });
    const exits = stepData(toFlow(input({ process: doc })), "read").exits;
    expect(exits.at(-1)).toEqual({ name: "encrypted", routed: true, implicit: false, declared: false });
  });

  it("takes a step's exits from its in-session proto doc over the step info", () => {
    const design = fixture("design");
    const path = "processes/process_supplier_invoice/proto/read_pdf.yaml";
    const proto = setIn(design.protos[path]!.doc, ["exits"], ["done", "encrypted"]);
    const flow = toFlow(input({ protos: { [path]: proto } }));
    expect(stepData(flow, "read").exits.map((x) => x.name)).toEqual(["done", "encrypted", "error"]);
  });

  it("propagates readOnly and counts issues per step and branch", () => {
    const issues: Issue[] = [
      { severity: "error", code: "E-REF-FIELD", message: "x", file: "processes/process_supplier_invoice/process.yaml",
        loc: ["edges", 3, "to", 0, "with", "dest"], span: [3, 37] },
      { severity: "warning", code: "W128", message: "y", file: "processes/process_supplier_invoice/proto/fix_fields.yaml",
        loc: ["env"], span: null },
    ];
    const flow = toFlow(input({ readOnly: true, issues }));
    expect(stepData(flow, "read").readOnly).toBe(true);
    expect(edgeData(flow, "b:3:0").hasIssues).toBe(true);
    expect(edgeData(flow, "b:3:1").hasIssues).toBe(false);
    expect(stepData(flow, "fix").issueCount).toBe(1);
    expect(edgeData(toFlow(input({ issues: null })), "b:3:0").hasIssues).toBe(false);
  });

  it("changes the structure key only when nodes or connections change", () => {
    const doc = fixture("design").process_file.doc as JsonObject;
    const base = structureKey(toFlow(input()));
    expect(structureKey(toFlow(input({ process: setIn(doc, ["edges", 3, "to", 0, "when"], "true") })))).toBe(base);
    expect(structureKey(toFlow(input({ process: setIn(doc, ["edges", 3, "to", 0, "step"], "fix") })))).not.toBe(base);
  });
});

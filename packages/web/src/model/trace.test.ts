import { describe, expect, it } from "vitest";
import type { Json, TraceEvent } from "../api/types";
import { fixture } from "../test/fixtures";
import { applyTraceEvent, emptyTree, type RunTree, type StepRun } from "./trace";

function build(events: TraceEvent[]): RunTree {
  return events.reduce(applyTraceEvent, emptyTree());
}

function byType(events: TraceEvent[], type: string): TraceEvent[] {
  return events.filter((e) => e.type === type);
}

describe("applyTraceEvent: the validate <-> fix loop", () => {
  const events = fixture("runEvents");
  const tree = build(events);

  it("one root per step run, in order, with occurrence keys", () => {
    expect(tree.roots.map((r) => r.key)).toEqual(["read#0", "extract#0", "validate#0", "fix#0", "validate#1", "save#0"]);
    expect(tree.roots.map((r) => r.run)).toEqual([1, 1, 1, 1, 2, 1]);
    expect(tree.roots.map((r) => r.via)).toEqual([null, "read.done[0]", "extract.done[0]", "validate.done[1]", "fix.done[0]", "validate.done[0]"]);
    expect(tree.open.size).toBe(0);
    expect(tree.last.get("validate")?.key).toBe("validate#1");
  });

  it("step.end fills exit, outputs, summary, timing, usage and model", () => {
    const extract = tree.roots[1] as StepRun;
    const end = events.find((e) => e.type === "step.end" && e.step === "extract") as TraceEvent;
    expect(extract).toMatchObject({
      status: "done", exit: "done", kind: "agentic", attempts: 1, span: 6, startedAt: events[5]?.ts,
      outputs: end.outputs, summary: end.summary, usage: end.usage, model: end.model,
    });
    expect(extract.durationMs).toBe((end.timings as { duration_ms: number }).duration_ms);
    expect(extract.inputs).toEqual((events[5] as TraceEvent).inputs);
  });

  it("edge.taken attaches to the run it left from", () => {
    expect(tree.roots.map((r) => r.edge && `${r.edge.from}[${r.edge.branch}] -> ${r.edge.to}`)).toEqual([
      "read.done[0] -> extract", "extract.done[0] -> validate", "validate.done[1] -> fix", "fix.done[0] -> validate",
      "validate.done[0] -> save", "save.done[0] -> $exit.done",
    ]);
  });

  it("model calls and logs go to their step; run.start/run.end are kept", () => {
    expect(tree.roots.map((r) => r.events.map((e) => e.type))).toEqual([
      ["worker.start"], ["model.call"], [], ["model.call"], [], ["step.log"],
    ]);
    expect(tree.started).toEqual(events[0]);
    expect(tree.finished).toEqual(events.at(-1));
    expect(tree.events).toEqual([]);
  });

  it("a running step is open until its step.end", () => {
    const partial = build(events.slice(0, 6));          // up to extract's step.start
    const extract = partial.roots.at(-1) as StepRun;
    expect(extract).toMatchObject({ step: "extract", status: "running", exit: null, finishedAt: null });
    expect(partial.open.get(6)).toBe(extract);
    expect(partial.finished).toBeNull();
  });

  it("never mutates the previous tree and shares untouched runs", () => {
    const before = build(events.slice(0, 20));          // save has started
    const snapshot = structuredClone(before);
    const after = applyTraceEvent(before, events[20] as TraceEvent);   // save's step.log
    expect(before).toEqual(snapshot);
    expect(after.roots[0]).toBe(before.roots[0]);
    expect(after.roots[5]).not.toBe(before.roots[5]);
    expect(after.roots[5]?.events).toHaveLength(1);
    expect(after.open.get(20)).toBe(after.roots[5]);
  });

  it("applying in two halves from an intermediate tree equals one pass", () => {
    const half = build(events.slice(0, 12));
    expect(events.slice(12).reduce(applyTraceEvent, half)).toEqual(tree);
  });
});

describe("applyTraceEvent: ProcessStep children", () => {
  const events = fixture("runEventsNested");
  const tree = build(events);

  it("children nest under their ProcessStep run", () => {
    expect(tree.roots.map((r) => r.step)).toEqual(["collect", "sub"]);
    const sub = tree.roots[1] as StepRun;
    expect(sub).toMatchObject({ kind: "process", id: "process:process_supplier_invoice", exit: "not_an_invoice", status: "done" });
    expect(sub.children.map((c) => [c.key, c.name, c.parent])).toEqual([["sub.read#0", "read", 7], ["sub.extract#0", "extract", 7]]);
  });

  it("child edges attach inside the child instance; the parent's own edge to the ProcessStep run", () => {
    const sub = tree.roots[1] as StepRun;
    expect(sub.children.map((c) => c.edge?.to)).toEqual(["extract", "$exit.not_an_invoice"]);
    expect(sub.edge?.to).toBe("$exit.nothing_to_do");
    expect(sub.children[1]?.events.map((e) => e.type)).toEqual(["model.call"]);
    expect(tree.roots[0]?.events.map((e) => e.type)).toEqual(["tool.call", "model.call"]);
  });

  it("the children held by the maps are the ones in the tree", () => {
    const sub = tree.roots[1] as StepRun;
    expect(tree.last.get("sub")).toBe(sub);
    expect(tree.last.get("sub.extract")).toBe(sub.children[1]);
  });

  it("a child whose ProcessStep run is unknown attaches to the roots", () => {
    const orphanStart = byType(events, "step.start").find((e) => e.step === "sub.read") as TraceEvent;
    const orphan = build([{ ...orphanStart, parent: 99, span: 50, seq: 50 }]);
    expect(orphan.roots.map((r) => r.step)).toEqual(["sub.read"]);
    const ended = applyTraceEvent(orphan, { ...(byType(events, "step.end")[1] as TraceEvent), span: 50, parent: 99, seq: 51 });
    expect(ended.roots[0]).toMatchObject({ step: "sub.read", status: "done", exit: "done" });
  });
});

describe("applyTraceEvent: errors and other events", () => {
  const ev = (fields: { seq: number; type: string; [k: string]: Json }): TraceEvent =>
    ({ v: 1, ts: "2026-09-27T10:00:00Z", run_id: "run_x", ...fields });
  const start = (seq: number, step: string): TraceEvent => ev({
    seq, type: "step.start", step, name: step, span: seq, parent: null, id: `p#${step}`, kind: "deterministic",
    run: 1, role: "node", via: null, inputs: {}, venv: "v",
  });

  it("an error exit or a timeout marks the run as error", () => {
    let t = build([start(1, "save"), start(3, "late")]);
    t = applyTraceEvent(t, ev({ seq: 2, type: "step.end", step: "save", span: 1, parent: null, exit: "error",
      timed_out: false, outputs: { cause: "exception", message: "boom" } }));
    t = applyTraceEvent(t, ev({ seq: 4, type: "step.end", step: "late", span: 3, parent: null, exit: null, timed_out: true, outputs: null }));
    expect(t.roots.map((r) => [r.status, r.exit])).toEqual([["error", "error"], ["error", null]]);
    expect(t.roots[0]?.outputs).toEqual({ cause: "exception", message: "boom" });
  });

  it("edge.check goes to the run the edge leaves; process.error and unknown step-less events stay run-level", () => {
    let t = build([start(1, "validate")]);
    t = applyTraceEvent(t, ev({ seq: 2, type: "step.end", step: "validate", span: 1, parent: null, exit: "done", timed_out: false, outputs: {} }));
    const check = ev({ seq: 3, type: "edge.check", process: "p", parent: null, edge: "validate.done", branch: 0,
      branch_key: "validate.done[save]", target: "save", take: false, reason: "total is negative" });
    const call = ev({ seq: 4, type: "model.call", step: "edge:validate.done[save]", span: null, parent: null });
    const error = ev({ seq: 5, type: "process.error", process: "p", parent: null, error: null, handler: "default" });
    const odd = ev({ seq: 6, type: "future.thing" });
    t = [check, call, error, odd].reduce(applyTraceEvent, t);
    expect(t.roots[0]?.events).toEqual([check, call]);
    expect(t.events).toEqual([error, odd]);
  });

  it("a step.end for an unknown span does not invent a run", () => {
    const t = applyTraceEvent(emptyTree(), ev({ seq: 9, type: "step.end", step: "ghost", span: 4, parent: null, exit: "done" }));
    expect(t.roots).toEqual([]);
    expect(t.events.map((e) => e.seq)).toEqual([9]);
  });
});

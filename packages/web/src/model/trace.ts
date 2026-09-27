// Run trace tree from PLAN §3.13 events (`$DRAFTS/07 §11.5` as amended by PLAN §10 amendment 1): consumes
// run.start, step.start, step.end, edge.taken, edge.check, model.call, tool.call, step.log and run.end; nesting via
// `span`/`parent`, display path = the `step` field ("sub.read"). Stub from WEB-SCAFFOLD; WEB-OPS implements it.
import type { Json, ModelRef, Summary, TraceEvent, TraceStepKind, Usage } from "../api/types";

export interface StepRun {
  key: string;                       // `${step}#${n}`, n = occurrence of this step path in the run (from 0)
  span: number;                      // seq of its step.start
  parent: number | null;             // span of the enclosing ProcessStep run
  step: string;                      // step path
  name: string;                      // node name
  id: string;                        // step id, or "process:<pid>"
  kind: TraceStepKind | null;
  run: number;                       // n-th run of this node in its process instance
  role: "node" | "on_error" | "finally";
  via: string | null;                // branch key it was entered through
  status: "running" | "done" | "error";
  startedAt: string;
  finishedAt: string | null;
  durationMs: number | null;
  attempts: number | null;
  inputs: Json | null;
  outputs: Json | null;
  exit: string | null;
  summary: Summary | null;
  usage: Usage | null;
  model: ModelRef | null;
  edge: { from: string; branch: number; name: string | null; to: string } | null;   // the edge it left by
  events: TraceEvent[];              // model.call, tool.call, step.log, edge.check and unknown types
  children: StepRun[];               // ProcessStep children
}

export interface RunTree {
  roots: StepRun[];
  open: Map<number, StepRun>;        // by span
  last: Map<string, StepRun>;        // latest run by step path
  started: TraceEvent | null;        // run.start
  finished: TraceEvent | null;       // run.end
  events: TraceEvent[];              // run-level events (no step)
}

export function emptyTree(): RunTree {
  throw new Error("not implemented");
}

/** Incremental, O(1) per event (copy-on-write of the touched path). */
export function applyTraceEvent(t: RunTree, ev: TraceEvent): RunTree {
  throw new Error("not implemented");
}

// Run trace tree from PLAN §3.13 events (`$DRAFTS/07 §11.5` as amended by PLAN §10 amendment 1): consumes
// run.start, step.start, step.end, edge.taken, edge.check, model.call, tool.call, step.log and run.end; nesting via
// `span`/`parent`, display path = the `step` field ("sub.read").
//
// Nodes are never mutated: an event copies the node it touches and each of its ancestors (and the two small indices),
// so a previous tree stays valid and untouched subtrees are shared. Execution is sequential, so the latest run of a
// step path is the one an `edge.taken`/`edge.check` leaves from.
import { isTrace, type Json, type ModelRef, type Summary, type TraceEvent, type TraceStepKind, type Usage } from "../api/types";

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
  return { roots: [], open: new Map(), last: new Map(), started: null, finished: null, events: [] };
}

/** Incremental: copy-on-write of the touched path (the run, its ancestors and the sibling lists holding them). */
export function applyTraceEvent(t: RunTree, ev: TraceEvent): RunTree {
  if (isTrace(ev, "run.start")) return { ...t, started: ev };
  if (isTrace(ev, "run.end")) return { ...t, finished: ev };
  if (isTrace(ev, "step.start")) return startRun(t, ev);
  if (isTrace(ev, "step.end")) return endRun(t, ev);
  if (isTrace(ev, "edge.taken")) {
    const run = leftBy(t, ev.parent ?? null, ev.from);
    if (run === undefined) return runLevel(t, ev);
    return replace(t, run, { ...run, edge: { from: ev.from, branch: ev.branch, name: ev.name ?? null, to: ev.to } });
  }
  const owner = ownerOf(t, ev);
  if (owner === undefined) return runLevel(t, ev);
  return replace(t, owner, { ...owner, events: [...owner.events, ev] });
}

function runLevel(t: RunTree, ev: TraceEvent): RunTree {
  return { ...t, events: [...t.events, ev] };
}

function startRun(t: RunTree, ev: TraceEvent): RunTree {
  const step = str(ev.step) ?? "";
  const prev = t.last.get(step);
  const run: StepRun = {
    key: `${step}#${prev === undefined ? 0 : occurrence(prev) + 1}`,
    span: num(ev.span) ?? ev.seq,
    parent: num(ev.parent),
    step,
    name: str(ev.name) ?? step.slice(step.lastIndexOf(".") + 1),
    id: str(ev.id) ?? "",
    kind: str(ev.kind) as TraceStepKind | null,
    run: num(ev.run) ?? 1,
    role: (str(ev.role) ?? "node") as StepRun["role"],
    via: str(ev.via),
    status: "running",
    startedAt: ev.ts,
    finishedAt: null,
    durationMs: null,
    attempts: null,
    inputs: ev.inputs ?? null,
    outputs: null,
    exit: null,
    summary: null,
    usage: null,
    model: null,
    edge: null,
    events: [],
    children: [],
  };
  const parent = run.parent === null ? undefined : t.open.get(run.parent);
  // A child whose ProcessStep run is unknown (an orphan) is shown at the top level.
  const next = parent === undefined
    ? { ...t, roots: [...t.roots, run], open: new Map(t.open), last: new Map(t.last) }
    : replace(t, parent, { ...parent, children: [...parent.children, run] });
  next.open.set(run.span, run);
  next.last.set(run.step, run);
  return next;
}

function endRun(t: RunTree, ev: TraceEvent): RunTree {
  const span = num(ev.span);
  const run = span === null ? undefined : t.open.get(span);
  if (run === undefined || span === null) return runLevel(t, ev);
  const exit = str(ev.exit);
  const timings = obj(ev.timings);
  const ended: StepRun = {
    ...run,
    kind: (str(ev.kind) as TraceStepKind | null) ?? run.kind,
    status: exit === "error" || ev.timed_out === true ? "error" : "done",
    finishedAt: str(timings?.ended_at) ?? ev.ts,
    durationMs: num(timings?.duration_ms),
    attempts: num(ev.attempts),
    outputs: ev.outputs ?? null,
    exit,
    summary: obj(ev.summary) as Summary | null,
    usage: obj(ev.usage) as Usage | null,
    model: obj(ev.model) as ModelRef | null,
  };
  const next = replace(t, run, ended);
  next.open.delete(span);
  return next;
}

/** The step run an event belongs to: by span (open, else the latest run of its path), edge checks by the edge. */
function ownerOf(t: RunTree, ev: TraceEvent): StepRun | undefined {
  const parent = num(ev.parent);
  if (ev.type === "edge.check") {
    const edge = str(ev.edge);
    return edge === null ? undefined : leftBy(t, parent, edge);
  }
  if (ev.type === "process.error") return parent === null ? undefined : t.open.get(parent);
  const step = str(ev.step);
  if (step !== null && step.startsWith("edge:")) {
    const branchKey = step.slice("edge:".length);            // model.call of an agentic edge check
    const bracket = branchKey.indexOf("[");
    return leftBy(t, parent, bracket === -1 ? branchKey : branchKey.slice(0, bracket));
  }
  const span = num(ev.span);
  const open = span === null ? undefined : t.open.get(span);
  if (open !== undefined) return open;
  return step === null ? undefined : t.last.get(step);
}

/** Latest run of the step an edge ("validate.done") leaves, inside the process instance whose ProcessStep is `parent`. */
function leftBy(t: RunTree, parent: number | null, edgeFrom: string): StepRun | undefined {
  const dot = edgeFrom.indexOf(".");
  const name = dot === -1 ? edgeFrom : edgeFrom.slice(0, dot);
  if (parent === null) return t.last.get(name);
  const prefix = t.open.get(parent)?.step;
  return prefix === undefined ? undefined : t.last.get(`${prefix}.${name}`);
}

function replace(t: RunTree, old: StepRun, next: StepRun): RunTree {
  const open = new Map(t.open);
  const last = new Map(t.last);
  let child = old;
  let updated = next;
  for (;;) {
    if (open.get(child.span) === child) open.set(child.span, updated);
    if (last.get(child.step) === child) last.set(child.step, updated);
    const parent = parentOf(t, child);
    if (parent === undefined) return { ...t, roots: swap(t.roots, child, updated), open, last };
    const parentNext = { ...parent, children: swap(parent.children, child, updated) };
    child = parent;
    updated = parentNext;
  }
}

function parentOf(t: RunTree, run: StepRun): StepRun | undefined {
  if (run.parent === null) return undefined;
  const dot = run.step.lastIndexOf(".");
  const candidate = t.open.get(run.parent) ?? (dot === -1 ? undefined : t.last.get(run.step.slice(0, dot)));
  if (candidate === undefined || candidate.span !== run.parent || !candidate.children.includes(run)) return undefined;
  return candidate;
}

function swap(runs: StepRun[], old: StepRun, next: StepRun): StepRun[] {
  const i = runs.lastIndexOf(old);
  if (i === -1) return runs;
  const copy = runs.slice();
  copy[i] = next;
  return copy;
}

function occurrence(run: StepRun): number {
  return Number(run.key.slice(run.key.lastIndexOf("#") + 1));
}

function num(v: Json | undefined): number | null {
  return typeof v === "number" ? v : null;
}

function str(v: Json | undefined): string | null {
  return typeof v === "string" ? v : null;
}

function obj(v: Json | undefined): { [k: string]: Json } | null {
  return v !== null && typeof v === "object" && !Array.isArray(v) ? v : null;
}

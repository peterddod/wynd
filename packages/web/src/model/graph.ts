// Raw docs + step info -> React Flow nodes and edges (`$DRAFTS/07 §7.5`; PLAN §10 amendment 2: one muted `x:$ignore`
// terminal per process). Positions are left at 0,0: the editor places nodes with `layout()` + per-browser overrides.
import type { Edge, Node } from "@xyflow/react";
import type { Issue, Json, StepInfo, StepPhase, TraceStepKind } from "../api/types";
import { branchesOnCycles } from "./cycles";
import { getIn } from "./json";
import type { LayoutEdge, LayoutNode } from "./layout";
import { type BranchView, edgesOf, IGNORE, processExits, stepNames } from "./processDoc";
import { exits as protoExits, inputNames } from "./protoDoc";

export interface StepNodeData extends Record<string, unknown> {
  kind: "step";
  name: string;
  use: string;
  stepKind: TraceStepKind | null;    // null = not compiled
  phase: StepPhase;
  tier: string | null;
  exits: { name: string; routed: boolean; implicit: boolean; declared: boolean }[];
  isEntry: boolean;
  isErrorHandler: boolean;
  isFinally: boolean;
  issueCount: number;
  readOnly: boolean;
}

/** `exit` is "$ignore" for the one muted ignore terminal. */
export interface TerminalNodeData extends Record<string, unknown> {
  kind: "exit";
  exit: string;
  declared: boolean;
}

export interface InputsNodeData extends Record<string, unknown> {
  kind: "inputs";
  fields: string[];
}

export interface BranchEdgeData extends Record<string, unknown> {
  edgeIndex: number;
  branchIndex: number;
  label: string;
  isElse: boolean;
  ignored: boolean;
  onCycle: boolean;
  maxTraversals: Json | null;
  hasIssues: boolean;
}

export type StepNodeType = Node<StepNodeData, "step">;
export type TerminalNodeType = Node<TerminalNodeData, "terminal">;
export type InputsNodeType = Node<InputsNodeData, "inputs">;
export type BranchEdgeType = Edge<BranchEdgeData, "branch">;

export interface FlowInput {
  process: Json;
  protos: Record<string, Json>;      // in-memory proto docs by path
  steps: Record<string, StepInfo>;   // controller step info by step key
  issues: Issue[] | null;            // null when stale
  readOnly: boolean;
}

export const INPUTS_ID = "in";
export const ENTRY_EDGE_ID = "entry";
const WHEN_MAX = 32;

function stepExitNames(name: string, info: StepInfo | undefined, protos: Record<string, Json>): string[] {
  const proto = info?.proto_path == null ? undefined : protos[info.proto_path];
  if (proto !== undefined && proto !== null) return protoExits(proto);
  return info?.interface.exits.map((x) => x.name) ?? [];
}

function finallyNames(doc: Json): string[] {
  const fin = getIn(doc, ["finally"]);
  if (!Array.isArray(fin)) return [];
  return fin.flatMap((item) => {
    if (typeof item === "string") return [item];
    const step = getIn(item, ["step"]);
    return typeof step === "string" ? [step] : [];
  });
}

function oneLine(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

export function branchLabel(b: BranchView, count: number): string {
  if (count === 1 && b.isElse) return "";
  const head = count > 1 ? `${b.index + 1}. ` : "";
  let cond = "else";
  if (b.when !== null) {
    const when = oneLine(b.when);
    cond = `if ${when.length > WHEN_MAX ? `${when.slice(0, WHEN_MAX)}…` : when}`;
  } else if (b.check !== null) {
    cond = "check";
  }
  return `${head}${cond}${b.name === null ? "" : ` [${b.name}]`}`;
}

export function toFlow(input: FlowInput): { nodes: Node[]; edges: Edge[] } {
  const { process, protos, steps, readOnly } = input;
  const names = stepNames(process);
  const edges = edgesOf(process);
  const cycles = branchesOnCycles(process);
  const routed = new Set(edges.map((e) => e.from));
  const counted = (input.issues ?? []).filter((i) => i.severity !== "info");
  const entry = getIn(process, ["entry"]);
  const onError = getIn(process, ["on_error"]);
  const fin = new Set(finallyNames(process));
  const at = { x: 0, y: 0 };

  const fields = inputNames(process);
  const nodes: Node[] = [{
    id: INPUTS_ID, type: "inputs", position: at, deletable: false, connectable: false,
    ariaLabel: `Process inputs: ${fields.length === 0 ? "none" : fields.join(", ")}`,
    data: { kind: "inputs", fields } satisfies InputsNodeData,
  }];

  for (const name of names) {
    const info = steps[name];
    const declared = stepExitNames(name, info, protos).filter((x) => x !== "error");
    const known = new Set([...declared, "error"]);
    const undeclared = edges.filter((e) => e.fromStep === name && !known.has(e.fromExit) && e.fromExit !== "")
      .map((e) => e.fromExit);
    const exits = [
      ...declared.map((x) => ({ name: x, routed: routed.has(`${name}.${x}`), implicit: false, declared: true })),
      { name: "error", routed: routed.has(`${name}.error`), implicit: true, declared: true },
      ...[...new Set(undeclared)].map((x) => ({ name: x, routed: true, implicit: false, declared: false })),
    ];
    const issueCount = counted.filter((i) => (info?.proto_path != null && i.file === info.proto_path)
      || (i.loc[0] === "steps" && i.loc[1] === name)).length;
    const data: StepNodeData = {
      kind: "step", name, use: String(getIn(process, ["steps", name, "use"]) ?? ""),
      stepKind: info?.kind ?? null, phase: info?.phase ?? "missing", tier: info?.lock?.tier ?? null,
      exits, isEntry: entry === name, isErrorHandler: onError === name, isFinally: fin.has(name), issueCount, readOnly,
    };
    nodes.push({
      id: `s:${name}`, type: "step", position: at, data,
      ariaLabel: `Step ${name}, ${data.stepKind ?? "not compiled"}, ${data.phase}, exits: ${exits.map((x) => x.name).join(", ")}`,
    });
  }

  const terminals = new Map<string, boolean>(processExits(process).map((x) => [x, true]));
  for (const e of edges) {
    for (const b of e.branches) {
      if (b.target.type === "exit" && !terminals.has(b.target.exit)) terminals.set(b.target.exit, false);
      if (b.target.type === "ignore") terminals.set(IGNORE, true);
    }
  }
  for (const [exit, declared] of terminals) {
    nodes.push({
      id: `x:${exit}`, type: "terminal", position: at,
      ariaLabel: exit === IGNORE ? "Ignored exit" : `Process exit ${exit}${declared ? "" : " (not declared)"}`,
      data: { kind: "exit", exit, declared } satisfies TerminalNodeData,
    });
  }

  const flowEdges: Edge[] = [];
  if (typeof entry === "string" && names.includes(entry)) {
    flowEdges.push({
      id: ENTRY_EDGE_ID, source: INPUTS_ID, sourceHandle: "o", target: `s:${entry}`, targetHandle: "t",
      selectable: false, deletable: false, focusable: false, reconnectable: false, className: "wg-entry-edge",
    });
  }
  for (const e of edges) {
    if (!names.includes(e.fromStep) || e.fromExit === "") continue;
    for (const b of e.branches) {
      let target: string;
      switch (b.target.type) {
        case "step":
          if (!names.includes(b.target.step)) continue;
          target = `s:${b.target.step}`;
          break;
        case "exit":
          target = `x:${b.target.exit}`;
          break;
        case "ignore":
          target = `x:${IGNORE}`;
          break;
      }
      const data: BranchEdgeData = {
        edgeIndex: e.index, branchIndex: b.index, label: branchLabel(b, e.branches.length), isElse: b.isElse,
        ignored: b.ignored, onCycle: cycles.has(`${e.index}:${b.index}`), maxTraversals: b.limits.max_traversals ?? null,
        hasIssues: counted.some((i) => i.loc[0] === "edges" && i.loc[1] === e.index && i.loc[2] === "to" && i.loc[3] === b.index),
      };
      flowEdges.push({
        id: `b:${e.index}:${b.index}`, type: "branch", source: `s:${e.fromStep}`, sourceHandle: `o:${e.fromExit}`,
        target, targetHandle: "t", data,
      });
    }
  }
  return { nodes, edges: flowEdges };
}

const STEP_W = 220;

function nodeHeight(n: Node): number {
  if (n.type !== "step") return 44;
  return 60 + 22 * (n.data as StepNodeData).exits.length;
}

/** Layout nodes and edges for `layout()`: `in` first, steps in document order, terminals last (in their order). */
export function layoutInput(flow: { nodes: Node[]; edges: Edge[] }): { nodes: LayoutNode[]; edges: LayoutEdge[] } {
  const nodes: LayoutNode[] = flow.nodes.map((n, i) => {
    switch (n.type) {
      case "inputs":
        return { id: n.id, w: 180, h: 44, order: -1, rank: "first" };
      case "terminal":
        return { id: n.id, w: 160, h: 44, order: i, rank: "last" };
      default:
        return { id: n.id, w: STEP_W, h: nodeHeight(n), order: i };
    }
  });
  const edges: LayoutEdge[] = flow.edges.map((e) => {
    const d = e.data as BranchEdgeData | undefined;
    return { source: e.source, target: e.target, order: d === undefined ? -1 : d.edgeIndex * 1000 + d.branchIndex };
  });
  return { nodes, edges };
}

/** Changes only when nodes or connections change (text edits do not re-layout). */
export function structureKey(flow: { nodes: Node[]; edges: Edge[] }): string {
  const ids = flow.nodes.map((n) => `${n.id}/${nodeHeight(n)}`).sort();
  const pairs = flow.edges.map((e) => `${e.source}>${e.target}`).sort();
  return `${ids.join(",")}|${pairs.join(",")}`;
}

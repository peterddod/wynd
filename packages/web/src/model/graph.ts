// Raw docs + step info -> React Flow nodes and edges (`$DRAFTS/07 §7.5`). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { Edge, Node } from "@xyflow/react";
import type { Issue, Json, StepInfo, StepPhase, TraceStepKind } from "../api/types";

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

export function toFlow(input: FlowInput): { nodes: Node[]; edges: Edge[] } {
  throw new Error("not implemented");
}

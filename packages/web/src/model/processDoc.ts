// Read views and ops over the raw process doc (`$DRAFTS/07 §7.2`; PLAN §10 amendment 2: `$ignore` targets and the M5
// branch fields `check`/`context`). Every op returns a new doc and leaves all other keys and their order untouched.
// Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { Json, Loc } from "../api/types";

export type { Loc };

export type Target = { type: "step"; step: string } | { type: "exit"; exit: string } | { type: "ignore" };

export interface BranchView {
  index: number;
  target: Target;
  when: string | null;               // null = no `when` key
  name: string | null;
  with: Record<string, Json>;
  limits: Record<string, Json>;
  check: string | null;              // M5 agentic edges: natural-language condition
  context: string[] | null;          // M5 agentic edges: pulled context
  isElse: boolean;                   // no `when` and no `check`
  ignored: boolean;                  // comes after the first else branch
  extra: Record<string, Json>;       // unknown keys on the branch (shorthand: unknown edge keys other than kind)
}

export interface EdgeView {
  index: number;
  from: string;
  fromStep: string;
  fromExit: string;
  shorthand: boolean;                // `to:` is a string
  kind: string;                      // edge.kind ?? "deterministic"
  branches: BranchView[];
}

export interface BranchPatch {
  when?: string | null;              // null deletes the key; "" is kept (pending condition)
  name?: string | null;
  target?: Target;
  check?: string | null;
  context?: string[] | null;
}

export function parseFrom(from: string): { step: string; exit: string } {
  throw new Error("not implemented");
}

/** "$exit.<name>" -> exit, "$ignore" -> ignore, else step. */
export function parseTarget(t: string): Target {
  throw new Error("not implemented");
}

export function targetString(t: Target): string {
  throw new Error("not implemented");
}

export function stepNames(doc: Json): string[] {
  throw new Error("not implemented");
}

export function edgesOf(doc: Json): EdgeView[] {
  throw new Error("not implemented");
}

/** -1 if none. */
export function edgeIndexFor(doc: Json, step: string, exit: string): number {
  throw new Error("not implemented");
}

/** Raw path of a branch field (handles shorthand edges). */
export function branchLoc(e: EdgeView, b: number, ...rest: Loc): Loc {
  throw new Error("not implemented");
}

/** ["edges", e, "to", b, ...rest] */
export function normalizedLoc(edgeIdx: number, b: number, ...rest: Loc): Loc {
  throw new Error("not implemented");
}

export function addStep(doc: Json, name: string, use: string): Json {
  throw new Error("not implemented");
}

export function removeStep(doc: Json, name: string): Json {
  throw new Error("not implemented");
}

export function renameStep(doc: Json, from: string, to: string): { doc: Json; rewritten: number; unparsed: Loc[] } {
  throw new Error("not implemented");
}

export function routeExit(
  doc: Json, step: string, exit: string, target: Target,
): { doc: Json; edgeIndex: number; branchIndex: number } {
  throw new Error("not implemented");
}

export function addBranch(doc: Json, edgeIndex: number, target: Target): { doc: Json; branchIndex: number } {
  throw new Error("not implemented");
}

export function expandShorthand(doc: Json, edgeIndex: number): Json {
  throw new Error("not implemented");
}

export function updateBranch(doc: Json, e: number, b: number, patch: BranchPatch): Json {
  throw new Error("not implemented");
}

export function setBranchMapValue(
  doc: Json, e: number, b: number, map: "with" | "limits", key: string, value: Json | undefined,
): Json {
  throw new Error("not implemented");
}

export function renameBranchMapKey(doc: Json, e: number, b: number, map: "with" | "limits", from: string, to: string): Json {
  throw new Error("not implemented");
}

export function moveBranch(doc: Json, e: number, from: number, to: number): Json {
  throw new Error("not implemented");
}

/** Removing the last branch removes the edge. */
export function removeBranch(doc: Json, e: number, b: number): Json {
  throw new Error("not implemented");
}

export function removeEdge(doc: Json, e: number): Json {
  throw new Error("not implemented");
}

/** "deterministic" deletes the key. */
export function setEdgeKind(doc: Json, e: number, kind: string): Json {
  throw new Error("not implemented");
}

export function setEntry(doc: Json, name: string): Json {
  throw new Error("not implemented");
}

export function setTop(doc: Json, key: "name" | "goal" | "latency" | "provider" | "on_error", value: Json | undefined): Json {
  throw new Error("not implemented");
}

/** env.base; deleting the last env key deletes env. */
export function setBase(doc: Json, base: string | undefined): Json {
  throw new Error("not implemented");
}

/** [] deletes the key. */
export function setFinally(doc: Json, steps: string[]): Json {
  throw new Error("not implemented");
}

export function setFieldMap(doc: Json, path: ["inputs"] | ["outputs", string], fields: [string, Json][]): Json {
  throw new Error("not implemented");
}

/** outputs key; every "$exit.<from>" target; examples[].exit */
export function renameProcessExit(doc: Json, from: string, to: string): Json {
  throw new Error("not implemented");
}

export function setExamples(doc: Json, examples: Json[]): Json {
  throw new Error("not implemented");
}

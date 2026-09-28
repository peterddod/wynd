// Validator issue -> UI selection (`$DRAFTS/07 §7.12`). Issue `loc`s are paths into the normalised document of
// `file` (edges always `to: [branches]`), so an edge index and branch index map straight to `b:<e>:<b>`.
import type { Issue, Loc } from "../api/types";

export type Selection =
  | { kind: "step"; step: string }
  | { kind: "branch"; e: number; b: number }
  | { kind: "edge"; e: number }
  | { kind: "exit"; exit: string }
  | { kind: "inputs" }
  | { kind: "process" };

export function selectionForIssue(
  issue: Issue, d: { processPath: string; stepsByProto: Record<string, string[]> },
): Selection {
  if (issue.file !== null && issue.file !== d.processPath) {
    const step = d.stepsByProto[issue.file]?.[0];
    return step === undefined ? { kind: "process" } : { kind: "step", step };
  }
  const [a, b, c, e] = issue.loc;
  if (a === "steps" && typeof b === "string") return { kind: "step", step: b };
  if (a === "edges" && typeof b === "number") {
    if (c === "to" && typeof e === "number") return { kind: "branch", e: b, b: e };
    return { kind: "edge", e: b };
  }
  if (a === "outputs" && typeof b === "string") return { kind: "exit", exit: b };
  if (a === "inputs" || a === "entry") return { kind: "inputs" };
  return { kind: "process" };
}

/** The URL `sel` value of a selection ("s:<step>", "b:<e>:<b>", "x:<exit>", "in"); null for the process overview. */
export function selectionParam(s: Selection): string | null {
  switch (s.kind) {
    case "step":
      return `s:${s.step}`;
    case "branch":
      return `b:${s.e}:${s.b}`;
    case "edge":
      return `b:${s.e}:0`;
    case "exit":
      return `x:${s.exit}`;
    case "inputs":
      return "in";
    case "process":
      return null;
  }
}

/** ("edges", 3, "to", 0, "with", "dest") -> "edges[3].to[0].with.dest" */
export function formatLoc(loc: Loc): string {
  return loc.map((p, i) => (typeof p === "number" ? `[${p}]` : i === 0 ? p : `.${p}`)).join("");
}

function startsWith(loc: Loc, prefix: Loc): boolean {
  return prefix.length <= loc.length && prefix.every((p, i) => loc[i] === p);
}

/** Prefix match on the normalised loc. */
export function issuesAt(issues: Issue[], file: string, prefix: Loc): Issue[] {
  return issues.filter((i) => i.file === file && startsWith(i.loc, prefix));
}

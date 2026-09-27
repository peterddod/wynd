// Validator issue -> UI selection (`$DRAFTS/07 §7.12`). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
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
  throw new Error("not implemented");
}

/** Prefix match on the normalised loc. */
export function issuesAt(issues: Issue[], file: string, prefix: Loc): Issue[] {
  throw new Error("not implemented");
}

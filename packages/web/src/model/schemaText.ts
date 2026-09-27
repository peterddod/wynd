// JSON Schema -> plain language (`$DRAFTS/07 §7.10`; path fields have `format === "path"`, PLAN §3.21 amendment 2).
// Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { Interface, JsonSchema } from "../api/types";

/** noun phrase, e.g. "a date", "a list of text values" */
export function describe(s: JsonSchema | null | undefined): string {
  throw new Error("not implemented");
}

/** "a (text), b (a number, optional)" */
export function describeFields(s: JsonSchema | null | undefined): string {
  throw new Error("not implemented");
}

export function stepSentence(iface: Interface): string {
  throw new Error("not implemented");
}

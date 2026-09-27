// Read views and ops over a raw proto-step doc (`$DRAFTS/07 §7.9`). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { Json } from "../api/types";

export type FieldsAt = ["inputs"] | ["outputs", string];

/** nested iff `exits` present, or outputs is a non-empty mapping whose values are all mappings; else flat */
export function outputsForm(doc: Json): "flat" | "nested" {
  throw new Error("not implemented");
}

/** `exits` if present; else nested -> keys(outputs); else ["done"] */
export function exits(doc: Json): string[] {
  throw new Error("not implemented");
}

export function inputNames(doc: Json): string[] {
  throw new Error("not implemented");
}

export function outputFields(doc: Json, exit: string): [string, Json][] {
  throw new Error("not implemented");
}

export function setInstruction(doc: Json, text: string): Json {
  throw new Error("not implemented");
}

export function setFieldType(doc: Json, where: FieldsAt, name: string, type: Json): Json {
  throw new Error("not implemented");
}

export function renameField(doc: Json, where: FieldsAt, from: string, to: string): Json {
  throw new Error("not implemented");
}

/** Schema only; example values are kept. */
export function removeField(doc: Json, where: FieldsAt, name: string): Json {
  throw new Error("not implemented");
}

export function addExit(doc: Json, name: string): Json {
  throw new Error("not implemented");
}

export function renameExit(doc: Json, from: string, to: string): Json {
  throw new Error("not implemented");
}

export function removeExit(doc: Json, name: string): Json {
  throw new Error("not implemented");
}

/** exit_codes; code "0".."255" | "*"; undefined deletes. */
export function setExitCode(doc: Json, code: string, exit: string | undefined): Json {
  throw new Error("not implemented");
}

export function setExamples(doc: Json, examples: Json[]): Json {
  throw new Error("not implemented");
}

export function setEnv(doc: Json, patch: { deps?: string[]; requires?: string | null }): Json {
  throw new Error("not implemented");
}

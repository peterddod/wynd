// Raw-doc helpers: immutable, path-preserving updates (`$DRAFTS/07 §7.1`). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { Json, JsonObject, Loc } from "../api/types";

export type { Json, JsonObject, Loc };

export function getIn(doc: Json | undefined, path: Loc): Json | undefined {
  throw new Error("not implemented");
}

/** Immutable; `undefined` deletes; an existing key keeps its position; a new key is appended. */
export function setIn(doc: Json | undefined, path: Loc, value: Json | undefined): Json {
  throw new Error("not implemented");
}

/** Renames a key keeping its position (returns a new object). */
export function renameKey(obj: JsonObject, from: string, to: string): JsonObject {
  throw new Error("not implemented");
}

export function deepEqual(a: Json | undefined, b: Json | undefined): boolean {
  throw new Error("not implemented");
}

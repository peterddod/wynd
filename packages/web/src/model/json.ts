// Raw-doc helpers: immutable, path-preserving updates (`$DRAFTS/07 §7.1`). An update that changes nothing returns
// the same reference, so "dirty" can be decided by reference (`$DRAFTS/07 §8.1`).
import type { Json, JsonObject, Loc } from "../api/types";

export type { Json, JsonObject, Loc };

export function isObject(v: Json | undefined): v is JsonObject {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function getIn(doc: Json | undefined, path: Loc): Json | undefined {
  let cur = doc;
  for (const key of path) {
    if (typeof key === "number") {
      if (!Array.isArray(cur)) return undefined;
      cur = cur[key];
    } else {
      if (!isObject(cur) || !Object.hasOwn(cur, key)) return undefined;
      cur = cur[key];
    }
    if (cur === undefined) return undefined;
  }
  return cur;
}

/** Immutable; `undefined` deletes (an array element is spliced out); an existing key keeps its position; a new key is
 *  appended. Missing parents are created on set (arrays for numeric keys) and left alone on delete. */
export function setIn(doc: Json | undefined, path: Loc, value: Json | undefined): Json {
  const out = update(doc, path, value);
  return out === undefined ? null : out;
}

function update(doc: Json | undefined, path: Loc, value: Json | undefined): Json | undefined {
  if (path.length === 0) return value;
  const [key, ...rest] = path as [string | number, ...Loc];
  if (typeof key === "number") {
    if (!Array.isArray(doc)) {
      if (value === undefined) return doc;
      const created: Json[] = [];
      created[key] = update(undefined, rest, value) as Json;
      return created;
    }
    const child = doc[key];
    const next = update(child, rest, value);
    if (next === child && (next !== undefined || key >= doc.length)) return doc;
    const copy = [...doc];
    if (next === undefined) copy.splice(key, 1);
    else copy[key] = next;
    return copy;
  }
  if (!isObject(doc)) {
    if (value === undefined) return doc;
    return { [key]: update(undefined, rest, value) as Json };
  }
  const had = Object.hasOwn(doc, key);
  const child = had ? doc[key] : undefined;
  const next = update(child, rest, value);
  if (next === child && (had || next === undefined)) return doc;
  const copy: JsonObject = {};
  for (const [k, v] of Object.entries(doc)) {
    if (k !== key) copy[k] = v;
    else if (next !== undefined) copy[k] = next;
  }
  if (!had && next !== undefined) copy[key] = next;
  return copy;
}

/** Renames a key keeping its position (a new object); an existing `to` key is replaced. Same object when `from` is
 *  absent or equal to `to`. */
export function renameKey(obj: JsonObject, from: string, to: string): JsonObject {
  if (from === to || !Object.hasOwn(obj, from)) return obj;
  const out: JsonObject = {};
  for (const [k, v] of Object.entries(obj)) {
    if (k === from) out[to] = v;
    else if (k !== to) out[k] = v;
  }
  return out;
}

/** Inserts or replaces `key` so that it sits right after `after` (appended when `after` is absent). */
export function setKeyAfter(obj: JsonObject, after: string, key: string, value: Json): JsonObject {
  if (Object.hasOwn(obj, key)) return obj[key] === value ? obj : { ...obj, [key]: value };
  if (!Object.hasOwn(obj, after)) return { ...obj, [key]: value };
  const out: JsonObject = {};
  for (const [k, v] of Object.entries(obj)) {
    out[k] = v;
    if (k === after) out[key] = value;
  }
  return out;
}

/** Structural equality; object key order is ignored. */
export function deepEqual(a: Json | undefined, b: Json | undefined): boolean {
  if (a === b) return true;
  if (Array.isArray(a)) {
    if (!Array.isArray(b) || a.length !== b.length) return false;
    return a.every((v, i) => deepEqual(v, b[i]));
  }
  if (isObject(a)) {
    if (!isObject(b)) return false;
    const keys = Object.keys(a);
    if (keys.length !== Object.keys(b).length) return false;
    return keys.every((k) => Object.hasOwn(b, k) && deepEqual(a[k], b[k]));
  }
  return false;
}

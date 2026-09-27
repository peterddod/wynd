// Read views and ops over a raw proto-step doc (`$DRAFTS/07 §7.9`). The outputs/exits rules are the spec's
// (PLAN §15 item 2), so they also apply to a process doc's `outputs`/`exits`/`examples`.
import type { Json, JsonObject } from "../api/types";
import { getIn, isObject, renameKey, setIn } from "./json";

export type FieldsAt = ["inputs"] | ["outputs", string];

function obj(v: Json | undefined): JsonObject {
  return isObject(v) ? v : {};
}

/** nested iff `exits` is declared, or outputs has a `done` key and every value is a mapping; else flat (done-only) */
export function outputsForm(doc: Json): "flat" | "nested" {
  if (Array.isArray(getIn(doc, ["exits"]))) return "nested";
  const outputs = obj(getIn(doc, ["outputs"]));
  const values = Object.values(outputs);
  if (Object.hasOwn(outputs, "done") && values.every((v) => isObject(v))) return "nested";
  return "flat";
}

/** `exits` if present; else nested -> keys(outputs); else ["done"] */
export function exits(doc: Json): string[] {
  const declared = getIn(doc, ["exits"]);
  if (Array.isArray(declared)) return declared.filter((x): x is string => typeof x === "string");
  if (outputsForm(doc) === "nested") return Object.keys(obj(getIn(doc, ["outputs"])));
  return ["done"];
}

export function inputNames(doc: Json): string[] {
  return Object.keys(obj(getIn(doc, ["inputs"])));
}

export function outputFields(doc: Json, exit: string): [string, Json][] {
  const outputs = obj(getIn(doc, ["outputs"]));
  if (outputsForm(doc) === "nested") return Object.entries(obj(outputs[exit]));
  return exit === "done" ? Object.entries(outputs) : [];
}

function fieldsPath(doc: Json, where: FieldsAt): string[] | null {
  if (where[0] === "inputs") return ["inputs"];
  if (outputsForm(doc) === "nested") return ["outputs", where[1]];
  return where[1] === "done" ? ["outputs"] : null;
}

export function setInstruction(doc: Json, text: string): Json {
  return setIn(doc, ["instruction"], text);
}

export function setFieldType(doc: Json, where: FieldsAt, name: string, type: Json): Json {
  const path = fieldsPath(doc, where);
  return path === null ? doc : setIn(doc, [...path, name], type);
}

/** Example keys follow the rename: examples[].inputs (inputs) or examples[exit == X].outputs (outputs X). */
export function renameField(doc: Json, where: FieldsAt, from: string, to: string): Json {
  const path = fieldsPath(doc, where);
  if (path === null) return doc;
  const fields = getIn(doc, path);
  let out = isObject(fields) ? setIn(doc, path, renameKey(fields, from, to)) : doc;
  const examples = getIn(out, ["examples"]);
  if (!Array.isArray(examples)) return out;
  examples.forEach((ex, i) => {
    if (!isObject(ex)) return;
    if (where[0] === "outputs" && (typeof ex.exit === "string" ? ex.exit : "done") !== where[1]) return;
    const key = where[0] === "inputs" ? "inputs" : "outputs";
    const values = ex[key];
    if (isObject(values)) out = setIn(out, ["examples", i, key], renameKey(values, from, to));
  });
  return out;
}

/** Schema only; example values are kept (shown as "not in schema"). */
export function removeField(doc: Json, where: FieldsAt, name: string): Json {
  const path = fieldsPath(doc, where);
  return path === null ? doc : setIn(doc, [...path, name], undefined);
}

/** flat -> nested: outputs = {done: <flat>, [name]: {}}, exits = ["done", name]; nested -> outputs[name] = {} (+ exits). */
export function addExit(doc: Json, name: string): Json {
  const current = exits(doc);
  if (current.includes(name)) return doc;
  if (outputsForm(doc) === "flat") {
    const flat = obj(getIn(doc, ["outputs"]));
    const out = setIn(doc, ["outputs"], { done: flat, [name]: {} });
    return setIn(out, ["exits"], ["done", name]);
  }
  let out = setIn(doc, ["outputs", name], {});
  if (Array.isArray(getIn(doc, ["exits"]))) out = setIn(out, ["exits"], [...current, name]);
  return out;
}

function renameExampleExits(doc: Json, from: string, to: string): Json {
  const examples = getIn(doc, ["examples"]);
  if (!Array.isArray(examples)) return doc;
  let out = doc;
  examples.forEach((ex, i) => {
    const exit = isObject(ex) ? (typeof ex.exit === "string" ? ex.exit : "done") : null;
    if (exit === from) out = setIn(out, ["examples", i, "exit"], to);
  });
  return out;
}

/** outputs key, `exits` entry, examples[].exit (a flat doc becomes nested: its only exit is renamed). */
export function renameExit(doc: Json, from: string, to: string): Json {
  if (from === to || !exits(doc).includes(from) || exits(doc).includes(to)) return doc;
  let out = doc;
  if (outputsForm(doc) === "flat") {
    out = setIn(out, ["outputs"], { [to]: obj(getIn(doc, ["outputs"])) });
    out = setIn(out, ["exits"], [to]);
  } else {
    const outputs = getIn(doc, ["outputs"]);
    if (isObject(outputs) && Object.hasOwn(outputs, from)) out = setIn(out, ["outputs"], renameKey(outputs, from, to));
    const declared = getIn(out, ["exits"]);
    if (Array.isArray(declared)) out = setIn(out, ["exits"], declared.map((x) => (x === from ? to : x)));
  }
  return renameExampleExits(out, from, to);
}

/** outputs key and `exits` entry; examples are kept (flagged by the editor). */
export function removeExit(doc: Json, name: string): Json {
  if (outputsForm(doc) === "flat") return doc;
  let out = setIn(doc, ["outputs", name], undefined);
  const declared = getIn(out, ["exits"]);
  if (Array.isArray(declared)) out = setIn(out, ["exits"], declared.filter((x) => x !== name));
  return out;
}

/** exit_codes; code "0".."255" | "*"; undefined deletes (the last one deletes the mapping). */
export function setExitCode(doc: Json, code: string, exit: string | undefined): Json {
  const out = setIn(doc, ["exit_codes", code], exit);
  const codes = getIn(out, ["exit_codes"]);
  return isObject(codes) && Object.keys(codes).length === 0 ? setIn(out, ["exit_codes"], undefined) : out;
}

export function setExamples(doc: Json, examples: Json[]): Json {
  if (examples.length === 0 && getIn(doc, ["examples"]) === undefined) return doc;
  return setIn(doc, ["examples"], examples);
}

/** `requires: null` deletes it; an empty env mapping is removed. */
export function setEnv(doc: Json, patch: { deps?: string[]; requires?: string | null }): Json {
  let out = doc;
  if (patch.deps !== undefined) out = setIn(out, ["env", "deps"], patch.deps);
  if (patch.requires !== undefined) out = setIn(out, ["env", "requires"], patch.requires ?? undefined);
  const env = getIn(out, ["env"]);
  return isObject(env) && Object.keys(env).length === 0 ? setIn(out, ["env"], undefined) : out;
}

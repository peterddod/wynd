// JSON Schema -> plain language (`$DRAFTS/07 §7.10`; path fields have `format === "path"`, PLAN §3.21 amendment 2).
// Schemas are pydantic's: `$ref`s into the root `$defs` are resolved, and an output model's `exit` const is not a field.
import type { Interface, JsonSchema } from "../api/types";

export type Defs = Record<string, JsonSchema> | undefined;

/** Follows `$ref: "#/$defs/<name>"` (and a single-item `allOf`, as pydantic wraps refs) against `defs`. */
export function resolveSchema(s: JsonSchema | null | undefined, defs: Defs): JsonSchema | null {
  if (s === null || s === undefined) return null;
  if (s.$ref !== undefined) {
    const target = defs?.[s.$ref.replace(/^#\/\$defs\//, "")];
    return target === undefined ? s : resolveSchema(target, defs);
  }
  if (s.allOf?.length === 1 && s.type === undefined) return resolveSchema(s.allOf[0], defs);
  return s;
}

/** The `$defs` a schema's refs resolve against: its own, else the enclosing root's. */
function defsOf(s: JsonSchema | null | undefined, defs: Defs): Defs {
  return s?.$defs ?? defs;
}

/** An object schema's fields, minus the `exit` discriminator of output models. */
export function schemaFields(
  s: JsonSchema | null | undefined, defs?: Defs,
): { name: string; schema: JsonSchema; required: boolean }[] {
  const d = defsOf(s, defs);
  const r = resolveSchema(s, d);
  const required = new Set(r?.required ?? []);
  return Object.entries(r?.properties ?? {})
    .filter(([name, p]) => !(name === "exit" && p.const !== undefined))
    .map(([name, p]) => ({ name, schema: resolveSchema(p, d) ?? p, required: required.has(name) }));
}

const PLURAL: Record<string, string> = {
  text: "text values",
  "a number": "numbers",
  "a whole number": "whole numbers",
  "a date": "dates",
  "a date and time": "dates and times",
  "a file path": "file paths",
  "yes or no": "yes/no values",
};

function describeIn(s: JsonSchema | null | undefined, defs: Defs): string {
  const d = defsOf(s, defs);
  const r = resolveSchema(s, d);
  if (r === null) return "any value";
  if (r.enum !== undefined) return `one of ${r.enum.map((v) => JSON.stringify(v)).join(", ")}`;
  const parts = r.anyOf ?? r.oneOf ?? (Array.isArray(r.type) ? r.type.map((t) => ({ type: t })) : undefined);
  if (parts !== undefined) {
    const nullable = parts.some((p) => p.type === "null");
    const text = parts.filter((p) => p.type !== "null").map((p) => describeIn(p, d)).join(" or ");
    if (text === "") return "nothing";
    return nullable ? `${text}, or empty` : text;
  }
  if (r.format === "path") return "a file path";
  if (r.format === "date") return "a date";
  if (r.format === "date-time") return "a date and time";
  switch (r.type) {
    case "string":
      return "text";
    case "number":
      return "a number";
    case "integer":
      return "a whole number";
    case "boolean":
      return "yes or no";
    case "null":
      return "nothing";
    case "array":
      return r.items === undefined ? "a list" : `a list of ${plural(r.items, d)}`;
    case "object": {
      if (r.properties === undefined) return "a record (any fields)";
      const fields = describeFieldsIn(r, d);
      return fields === "" ? "an empty record" : `a record with ${fields}`;
    }
  }
  return "any value";
}

function plural(items: JsonSchema, defs: Defs): string {
  const d = defsOf(items, defs);
  const r = resolveSchema(items, d);
  if (r?.type === "object" && r.properties !== undefined) return `records, each with ${describeFieldsIn(r, d)}`;
  if (r?.type === "object") return "records (any fields)";
  const one = describeIn(items, d);
  if (one.startsWith("a list")) return `lists${one.slice("a list".length)}`;
  return PLURAL[one] ?? `values (each ${one})`;
}

function fieldPhrases(s: JsonSchema | null | undefined, defs: Defs): string[] {
  const d = defsOf(s, defs);
  return schemaFields(s, d).map((f) => `${f.name} (${describeIn(f.schema, d)}${f.required ? "" : ", optional"})`);
}

function describeFieldsIn(s: JsonSchema | null | undefined, defs: Defs): string {
  return fieldPhrases(s, defs).join(", ");
}

/** noun phrase, e.g. "a date", "a list of text values" */
export function describe(s: JsonSchema | null | undefined): string {
  return describeIn(s, undefined);
}

/** "a (text), b (a number, optional)" */
export function describeFields(s: JsonSchema | null | undefined): string {
  return describeFieldsIn(s, undefined);
}

function andJoin(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;
}

/** "Takes invoice_text (text). Finishes with done, returning total (a number) and due_date (a date); or
 *  not_an_invoice, returning nothing." */
export function stepSentence(iface: Interface): string {
  const inputs = fieldPhrases(iface.inputs, undefined);
  const takes = inputs.length === 0 ? "Takes nothing." : `Takes ${andJoin(inputs)}.`;
  const exits = iface.exits.map((x) => {
    const fields = fieldPhrases(x.schema, undefined);
    return `${x.name}, returning ${fields.length === 0 ? "nothing" : andJoin(fields)}`;
  });
  if (exits.length === 0) return takes;
  const finishes = exits.length === 1 ? exits[0] : `${exits.slice(0, -1).join("; ")}; or ${exits.at(-1)}`;
  return `${takes} Finishes with ${finishes}.`;
}

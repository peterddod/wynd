// Example and run value parsing (`$DRAFTS/07 §7.11`).
import type { Interface, Json, JsonSchema } from "../api/types";
import { isObject } from "./json";
import { resolveSchema } from "./schemaText";

/** valid JSON -> parsed; else the raw string */
export function parseLoose(text: string): Json {
  try {
    return JSON.parse(text) as Json;
  } catch {
    return text;
  }
}

/** strings raw; others JSON.stringify */
export function formatLoose(v: Json): string {
  return typeof v === "string" ? v : JSON.stringify(v);
}

const DATE = /^\d{4}-\d{2}-\d{2}$/;

function typeOf(s: JsonSchema): string | null {
  if (s.format === "date" || s.format === "date-time" || s.format === "path") return "string";
  return typeof s.type === "string" ? s.type : null;
}

/** The schema's non-null alternative when it is `T | null` (`anyOf` or a type list), with `nullable` set. */
function unwrapNullable(s: JsonSchema): { schema: JsonSchema; nullable: boolean } {
  const parts = s.anyOf ?? s.oneOf ?? (Array.isArray(s.type) ? s.type.map((t) => ({ ...s, type: t })) : undefined);
  if (parts === undefined) return { schema: s, nullable: false };
  const rest = parts.filter((p) => p.type !== "null");
  const nullable = rest.length < parts.length;
  return { schema: rest.length === 1 ? (rest[0] as JsonSchema) : { anyOf: rest }, nullable };
}

export function parseTyped(text: string, s: JsonSchema | null): { ok: true; value: Json } | { ok: false; error: string } {
  const resolved = resolveSchema(s, s?.$defs);
  if (resolved === null) return { ok: true, value: parseLoose(text) };
  const { schema, nullable } = unwrapNullable(resolved);
  if (nullable && (text.trim() === "" || text.trim() === "null")) return { ok: true, value: null };
  const t = text.trim();
  switch (typeOf(schema)) {
    case "string":
      if (schema.format === "date" && !DATE.test(t)) return { ok: false, error: "expected a date (YYYY-MM-DD)" };
      return { ok: true, value: text };
    case "number":
      if (t === "" || !Number.isFinite(Number(t))) return { ok: false, error: "expected a number" };
      return { ok: true, value: Number(t) };
    case "integer":
      if (t === "" || !Number.isInteger(Number(t))) return { ok: false, error: "expected a whole number" };
      return { ok: true, value: Number(t) };
    case "boolean":
      if (t !== "true" && t !== "false") return { ok: false, error: "expected true or false" };
      return { ok: true, value: t === "true" };
  }
  let value: Json;
  try {
    value = JSON.parse(text) as Json;
  } catch (err) {
    return { ok: false, error: `invalid JSON: ${(err as Error).message}` };
  }
  if (schema.type === "object" && !isObject(value)) return { ok: false, error: "expected a JSON object" };
  if (schema.type === "array" && !Array.isArray(value)) return { ok: false, error: "expected a JSON list" };
  return { ok: true, value };
}

const VALUE_MAX = 24;

function show(v: Json): string {
  const text = JSON.stringify(v) ?? "null";
  if (text.length <= VALUE_MAX) return text;
  return typeof v === "string" ? `${text.slice(0, VALUE_MAX - 1)}…"` : `${text.slice(0, VALUE_MAX - 1)}…`;
}

function ordered(values: Record<string, Json>, schema: JsonSchema | null | undefined): [string, Json][] {
  const keys = Object.keys(resolveSchema(schema, schema?.$defs)?.properties ?? {});
  const first = keys.filter((k) => Object.hasOwn(values, k));
  const rest = Object.keys(values).filter((k) => !first.includes(k));
  return [...first, ...rest].map((k) => [k, values[k] as Json]);
}

function assignments(pairs: [string, Json][]): string {
  return pairs.map(([k, v]) => `${k} = ${show(v)}`).join(", ");
}

/** 'Given invoice_text = "Dear customer…" → done with invoice_number = "INV-1042", total = 1200.5' */
export function exampleSentence(ex: Json, iface: Interface | null): string {
  const e = isObject(ex) ? ex : {};
  const inputs = isObject(e.inputs) ? e.inputs : {};
  const outputs = isObject(e.outputs) ? e.outputs : {};
  const exit = typeof e.exit === "string" ? e.exit : "done";
  const given = Object.keys(inputs).length === 0 ? "Given no inputs" : `Given ${assignments(ordered(inputs, iface?.inputs))}`;
  const exitSchema = iface?.exits.find((x) => x.name === exit)?.schema;
  const returned = Object.keys(outputs).length === 0 ? "" : ` with ${assignments(ordered(outputs, exitSchema))}`;
  return `${given} → ${exit}${returned}`;
}

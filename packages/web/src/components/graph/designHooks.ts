// Shared reads over the design session for the graph, inspector and settings components.
import { useDesign } from "../../api/context";
import type { Interface, Json, JsonSchema, ProcessInterface } from "../../api/types";
import { getIn, isObject } from "../../model/json";
import type { Target } from "../../model/processDoc";
import { exits as protoExits, inputNames } from "../../model/protoDoc";
import { resolveSchema, schemaFields } from "../../model/schemaText";
import type { DesignState } from "../../state/design";
import { useStore } from "../../state/store";
import type { FieldRow } from "../inspector/WithMappingEditor";

/** The whole design state (null while no process is open); re-renders on every change. */
export function useDesignState(): DesignState | null {
  return useStore(useDesign().store, (s) => s);
}

export function isReadOnly(s: DesignState): boolean {
  return s.lockedBy !== null || s.parseError !== null;
}

/** In-memory proto docs by path (deleted files left out). */
export function protoDocs(s: DesignState): Record<string, Json> {
  const out: Record<string, Json> = {};
  for (const [path, f] of Object.entries(s.protos)) if (f.deleted !== true) out[path] = f.doc;
  return out;
}

export type Sel =
  | { kind: "step"; step: string }
  | { kind: "branch"; e: number; b: number }
  | { kind: "exit"; exit: string }
  | { kind: "inputs" };

/** URL `sel`: "s:<step>" | "b:<edgeIdx>:<branchIdx>" | "x:<exit>" | "in". */
export function parseSel(sel: string | null): Sel | null {
  if (sel === null) return null;
  if (sel === "in") return { kind: "inputs" };
  if (sel.startsWith("s:")) return { kind: "step", step: sel.slice(2) };
  if (sel.startsWith("x:")) return { kind: "exit", exit: sel.slice(2) };
  const m = /^b:(\d+):(\d+)$/.exec(sel);
  return m === null ? null : { kind: "branch", e: Number(m[1]), b: Number(m[2]) };
}

/** The proto file of a step key: from the step info, or (for a step added in this session) the local convention. */
export function protoPathOf(s: DesignState, step: string): string | null {
  const known = s.steps[step]?.proto_path;
  if (known !== undefined && known !== null) return known;
  const use = getIn(s.process.doc, ["steps", step, "use"]);
  if (typeof use !== "string" || !use.startsWith("./steps/")) return null;
  const path = s.conventions.local_proto_path.replace("{name}", use.slice("./steps/".length));
  return s.protos[path] !== undefined && s.protos[path]?.deleted !== true ? path : null;
}

/** Proto path -> the step keys of the open process using it. */
export function stepsByProto(s: DesignState): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  const steps = getIn(s.process.doc, ["steps"]);
  for (const step of isObject(steps) ? Object.keys(steps) : []) {
    const path = protoPathOf(s, step);
    if (path !== null) out[path] = [...(out[path] ?? []), step];
  }
  return out;
}

/** The declared exits of a step: its in-session proto doc first, else the controller's interface. */
export function stepExits(s: DesignState, step: string): string[] {
  const path = protoPathOf(s, step);
  const doc = path === null ? undefined : s.protos[path]?.doc;
  if (doc !== undefined && doc !== null) return protoExits(doc);
  return s.steps[step]?.interface.exits.map((x) => x.name) ?? [];
}

/** The input fields a branch's `with` maps onto; null when the target's interface is unknown. */
export function targetFields(s: DesignState, target: Target): FieldRow[] | null {
  switch (target.type) {
    case "ignore":
      return [];
    case "exit": {
      const schema = s.iface?.outputs[target.exit];
      if (schema === undefined || schema === null) return null;
      return schemaFields(schema).map((f) => ({ name: f.name, schema: f.schema, required: f.required }));
    }
    case "step": {
      const info = s.steps[target.step];
      const inputs = info?.interface.inputs ?? null;
      const typed = new Map(schemaFields(inputs).map((f) => [f.name, f]));
      const path = protoPathOf(s, target.step);
      const doc = path === null ? undefined : s.protos[path]?.doc;
      if (doc !== undefined && doc !== null) {
        return inputNames(doc).map((name) => ({ name, schema: typed.get(name)?.schema ?? null, required: typed.get(name)?.required ?? true }));
      }
      if (inputs === null) return null;
      return [...typed.values()].map((f) => ({ name: f.name, schema: f.schema, required: f.required }));
    }
  }
}

/** The process interface as a step `Interface` (for the examples editor). */
export function processInterface(iface: ProcessInterface | null): Interface | null {
  if (iface === null) return null;
  return {
    inputs: iface.inputs,
    exits: Object.entries(iface.outputs).map(([name, schema]) => ({ name, schema })),
    source: "process",
  };
}

/** A property schema of an object schema, with `$ref`s resolved against the root. */
export function propertySchema(root: JsonSchema | null, name: string): JsonSchema | null {
  const resolved = resolveSchema(root, root?.$defs);
  return resolveSchema(resolved?.properties?.[name], root?.$defs);
}

export function classes(...names: (string | false | null | undefined)[]): string {
  return names.filter(Boolean).join(" ");
}

const STEP_KEY = /^[a-z_][a-z0-9_]*$/;
const RESERVED = new Set([
  "steps", "process", "env", "run", "previous", "edges", "and", "or", "not", "in", "if", "then", "elif", "else", "true",
  "false", "null",
]);

/** Client-side hint for a step key (PLAN §3.1 `StepKey`); the validator is authoritative. */
export function stepNameProblem(name: string, existing: string[]): string | null {
  if (name === "") return "Enter a name.";
  if (!STEP_KEY.test(name)) return "Use lower-case letters, digits and _, starting with a letter or _.";
  if (RESERVED.has(name)) return `"${name}" is a reserved word.`;
  if (existing.includes(name)) return `A step named "${name}" already exists.`;
  return null;
}

// Read views and ops over the raw process doc (`$DRAFTS/07 §7.2`; PLAN §10 amendment 2: `$ignore` targets and the M5
// branch fields `check`/`context`). Every op returns a new doc and leaves all other keys and their order untouched;
// an op that changes nothing returns the same doc.
//
// Raw edge forms (PLAN §3.3): `to: <target>` (shorthand; `with`/`limits`/`check`/`context` sit on the edge), or
// `to: [branch…]` where a branch is an object or a bare target string. The UI never collapses a list back to shorthand.
import type { Json, JsonObject, Loc } from "../api/types";
import { renameStepInExpr } from "./expr";
import { getIn, isObject, renameKey, setIn, setKeyAfter } from "./json";
import { exits as docExits, outputsForm, renameExit } from "./protoDoc";

export type { Loc };

export type Target = { type: "step"; step: string } | { type: "exit"; exit: string } | { type: "ignore" };

export interface BranchView {
  index: number;
  target: Target;
  when: string | null;               // null = no `when` key
  name: string | null;
  with: Record<string, Json>;
  limits: Record<string, Json>;
  check: string | null;              // M5 agentic edges: natural-language condition
  context: string[] | null;          // M5 agentic edges: pulled context
  isElse: boolean;                   // no `when` and no `check`
  ignored: boolean;                  // comes after the first else branch
  extra: Record<string, Json>;       // unknown keys on the branch (shorthand: unknown edge keys other than kind)
}

export interface EdgeView {
  index: number;
  from: string;
  fromStep: string;
  fromExit: string;
  shorthand: boolean;                // `to:` is a string
  kind: string;                      // edge.kind ?? "deterministic"
  branches: BranchView[];
}

export interface BranchPatch {
  when?: string | null;              // null deletes the key; "" is kept (pending condition)
  name?: string | null;
  target?: Target;
  check?: string | null;
  context?: string[] | null;
}

export const EXIT_PREFIX = "$exit.";
export const IGNORE = "$ignore";

const EDGE_KEYS = new Set(["from", "to", "kind"]);
const LIFTED = ["with", "limits", "check", "context"] as const;
const BRANCH_KEYS = new Set(["step", "when", "with", "limits", "name", "check", "context"]);

export function parseFrom(from: string): { step: string; exit: string } {
  const dot = from.indexOf(".");
  if (dot === -1) return { step: from, exit: "" };
  return { step: from.slice(0, dot), exit: from.slice(dot + 1) };
}

/** "$exit.<name>" -> exit, "$ignore" -> ignore, else step. */
export function parseTarget(t: string): Target {
  if (t === IGNORE) return { type: "ignore" };
  if (t.startsWith(EXIT_PREFIX)) return { type: "exit", exit: t.slice(EXIT_PREFIX.length) };
  return { type: "step", step: t };
}

export function targetString(t: Target): string {
  switch (t.type) {
    case "step":
      return t.step;
    case "exit":
      return `${EXIT_PREFIX}${t.exit}`;
    case "ignore":
      return IGNORE;
  }
}

function obj(v: Json | undefined): JsonObject {
  return isObject(v) ? v : {};
}

function list(v: Json | undefined): Json[] {
  return Array.isArray(v) ? v : [];
}

function str(v: Json | undefined): string | null {
  return typeof v === "string" ? v : null;
}

function strings(v: Json | undefined): string[] | null {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : null;
}

/** A `when:` value as text: booleans (YAML literals) are shown as `true`/`false`. */
function whenText(v: Json | undefined): string | null {
  if (v === undefined || v === null) return null;
  return typeof v === "string" ? v : JSON.stringify(v);
}

export function stepNames(doc: Json): string[] {
  return Object.keys(obj(getIn(doc, ["steps"])));
}

function branchView(raw: JsonObject, index: number, known: Set<string>): Omit<BranchView, "ignored"> {
  const when = whenText(raw.when);
  const check = str(raw.check);
  const extra: Record<string, Json> = {};
  for (const [k, v] of Object.entries(raw)) if (!known.has(k)) extra[k] = v;
  return {
    index,
    target: parseTarget(str(raw.step) ?? ""),
    when,
    name: str(raw.name),
    with: obj(raw.with),
    limits: obj(raw.limits),
    check,
    context: strings(raw.context),
    isElse: when === null && check === null,
    extra,
  };
}

function edgeView(edge: Json, index: number): EdgeView {
  const e = obj(edge);
  const from = str(e.from) ?? "";
  const { step, exit } = parseFrom(from);
  const to = e.to;
  let branches: Omit<BranchView, "ignored">[] = [];
  if (typeof to === "string") {
    const known = new Set([...EDGE_KEYS, ...LIFTED]);
    const raw: JsonObject = { step: to };
    for (const k of LIFTED) if (Object.hasOwn(e, k)) raw[k] = e[k] as Json;
    const view = branchView(raw, 0, BRANCH_KEYS);
    const extra: Record<string, Json> = {};
    for (const [k, v] of Object.entries(e)) if (!known.has(k)) extra[k] = v;
    branches = [{ ...view, extra }];
  } else if (Array.isArray(to)) {
    branches = to.map((item, i) => branchView(typeof item === "string" ? { step: item } : obj(item), i, BRANCH_KEYS));
  }
  const firstElse = branches.findIndex((b) => b.isElse);
  return {
    index,
    from,
    fromStep: step,
    fromExit: exit,
    shorthand: typeof to === "string",
    kind: str(e.kind) ?? "deterministic",
    branches: branches.map((b) => ({ ...b, ignored: firstElse !== -1 && b.index > firstElse })),
  };
}

export function edgesOf(doc: Json): EdgeView[] {
  return list(getIn(doc, ["edges"])).map(edgeView);
}

export function edgeAt(doc: Json, e: number): EdgeView | null {
  const edge = getIn(doc, ["edges", e]);
  return edge === undefined ? null : edgeView(edge, e);
}

/** -1 if none. */
export function edgeIndexFor(doc: Json, step: string, exit: string): number {
  const from = `${step}.${exit}`;
  return list(getIn(doc, ["edges"])).findIndex((edge) => obj(edge).from === from);
}

/** `validate.done[1]` / `validate.done[fix]` (PLAN §3.1 branch key). */
export function branchKey(e: EdgeView, b: number): string {
  const name = e.branches[b]?.name;
  return `${e.from}[${name ?? b}]`;
}

/** Raw path of a branch field (handles shorthand edges: the target is `to`, the other fields sit on the edge). */
export function branchLoc(e: EdgeView, b: number, ...rest: Loc): Loc {
  if (!e.shorthand) return ["edges", e.index, "to", b, ...rest];
  if (rest[0] === "step") return ["edges", e.index, "to", ...rest.slice(1)];
  return ["edges", e.index, ...rest];
}

/** ["edges", e, "to", b, ...rest] */
export function normalizedLoc(edgeIdx: number, b: number, ...rest: Loc): Loc {
  return ["edges", edgeIdx, "to", b, ...rest];
}

/** A list item that is a bare target string becomes `{step: <target>}` so fields can be written on it. */
function objectifyBranch(doc: Json, e: number, b: number): Json {
  const item = getIn(doc, ["edges", e, "to", b]);
  return typeof item === "string" ? setIn(doc, ["edges", e, "to", b], { step: item }) : doc;
}

function mapEdges(doc: Json, fn: (edge: Json, i: number) => Json | undefined): Json {
  const edges = getIn(doc, ["edges"]);
  if (!Array.isArray(edges)) return doc;
  let changed = false;
  const next: Json[] = [];
  edges.forEach((edge, i) => {
    const out = fn(edge, i);
    if (out !== edge) changed = true;
    if (out !== undefined) next.push(out);
  });
  return changed ? setIn(doc, ["edges"], next) : doc;
}

export function addStep(doc: Json, name: string, use: string): Json {
  let out = setIn(doc, ["steps", name], { use });
  if (!str(getIn(out, ["entry"]))) out = setIn(out, ["entry"], name);
  return out;
}

function targetsStep(item: Json, step: string): boolean {
  const t = typeof item === "string" ? item : str(obj(item).step);
  return t === step;
}

export function removeStep(doc: Json, name: string): Json {
  let out = setIn(doc, ["steps", name], undefined);
  out = mapEdges(out, (edge) => {
    const e = obj(edge);
    if (parseFrom(str(e.from) ?? "").step === name) return undefined;
    if (typeof e.to === "string") return e.to === name ? undefined : edge;
    if (!Array.isArray(e.to)) return edge;
    const kept = e.to.filter((item) => !targetsStep(item, name));
    if (kept.length === e.to.length) return edge;
    return kept.length === 0 ? undefined : { ...e, to: kept };
  });
  if (getIn(out, ["entry"]) === name) out = setIn(out, ["entry"], undefined);
  if (getIn(out, ["on_error"]) === name) out = setIn(out, ["on_error"], undefined);
  const fin = getIn(out, ["finally"]);
  if (Array.isArray(fin)) {
    const kept = fin.filter((item) => !targetsStep(item, name));
    if (kept.length !== fin.length) out = setIn(out, ["finally"], kept.length === 0 ? undefined : kept);
  }
  return out;
}

/** Rewrites every expression string inside `v` (recursively); counts rewrites, records unlexable locations. */
function rewriteExprs(v: Json, loc: Loc, from: string, to: string, acc: { rewritten: number; unparsed: Loc[] }): Json {
  if (typeof v === "string") {
    const next = renameStepInExpr(v, from, to);
    if (next === null) {
      acc.unparsed.push(loc);
      return v;
    }
    if (next !== v) acc.rewritten += 1;
    return next;
  }
  if (Array.isArray(v)) {
    let changed = false;
    const out = v.map((x, i) => {
      const y = rewriteExprs(x, [...loc, i], from, to, acc);
      if (y !== x) changed = true;
      return y;
    });
    return changed ? out : v;
  }
  if (isObject(v)) {
    let changed = false;
    const out: JsonObject = {};
    for (const [k, x] of Object.entries(v)) {
      const y = rewriteExprs(x, [...loc, k], from, to, acc);
      if (y !== x) changed = true;
      out[k] = y;
    }
    return changed ? out : v;
  }
  return v;
}

const EXPR_KEYS = ["when", "with", "limits", "context"] as const;

function rewriteFields(item: JsonObject, loc: Loc, from: string, to: string, acc: { rewritten: number; unparsed: Loc[] },
                       keys: readonly string[]): JsonObject {
  let out = item;
  for (const k of keys) {
    if (!Object.hasOwn(out, k)) continue;
    const v = out[k] as Json;
    const y = rewriteExprs(v, [...loc, k], from, to, acc);
    if (y !== v) out = { ...out, [k]: y };
  }
  return out;
}

export function renameStep(doc: Json, from: string, to: string): { doc: Json; rewritten: number; unparsed: Loc[] } {
  const acc = { rewritten: 0, unparsed: [] as Loc[] };
  const steps = getIn(doc, ["steps"]);
  let out = isObject(steps) ? setIn(doc, ["steps"], renameKey(steps, from, to)) : doc;
  const retarget = (t: string): string => (t === from ? to : t);
  out = mapEdges(out, (edge, i) => {
    if (!isObject(edge)) return edge;
    let e: JsonObject = edge;
    const parsed = parseFrom(str(e.from) ?? "");
    if (parsed.step === from) e = { ...e, from: `${to}.${parsed.exit}` };
    if (typeof e.to === "string") {
      if (e.to === from) e = { ...e, to };
      e = rewriteFields(e, ["edges", i], from, to, acc, ["with", "limits", "context"]);
    } else if (Array.isArray(e.to)) {
      let changed = false;
      const branches = e.to.map((item, b) => {
        if (typeof item === "string") {
          const t = retarget(item);
          if (t !== item) changed = true;
          return t;
        }
        if (!isObject(item)) return item;
        let br = item;
        if (typeof br.step === "string" && br.step === from) br = { ...br, step: to };
        br = rewriteFields(br, ["edges", i, "to", b], from, to, acc, EXPR_KEYS);
        if (br !== item) changed = true;
        return br;
      });
      if (changed) e = { ...e, to: branches };
    }
    return e === edge ? edge : e;
  });
  if (getIn(out, ["entry"]) === from) out = setIn(out, ["entry"], to);
  if (getIn(out, ["on_error"]) === from) out = setIn(out, ["on_error"], to);
  const fin = getIn(out, ["finally"]);
  if (Array.isArray(fin)) {
    let changed = false;
    const next = fin.map((item, i) => {
      if (typeof item === "string") {
        if (item !== from) return item;
        changed = true;
        return to;
      }
      if (!isObject(item)) return item;
      let f = item;
      if (f.step === from) f = { ...f, step: to };
      f = rewriteFields(f, ["finally", i], from, to, acc, ["with"]);
      if (f !== item) changed = true;
      return f;
    });
    if (changed) out = setIn(out, ["finally"], next);
  }
  return { doc: out, rewritten: acc.rewritten, unparsed: acc.unparsed };
}

/** No edge for step.exit: a new shorthand edge; otherwise a new branch (see `addBranch`). */
export function routeExit(
  doc: Json, step: string, exit: string, target: Target,
): { doc: Json; edgeIndex: number; branchIndex: number } {
  const e = edgeIndexFor(doc, step, exit);
  if (e === -1) {
    const edges = list(getIn(doc, ["edges"]));
    return {
      doc: setIn(doc, ["edges"], [...edges, { from: `${step}.${exit}`, to: targetString(target) }]),
      edgeIndex: edges.length,
      branchIndex: 0,
    };
  }
  const { doc: out, branchIndex } = addBranch(doc, e, target);
  return { doc: out, edgeIndex: e, branchIndex };
}

/** Inserted before an existing else with `when: ""` (the UI focuses it); otherwise appended (it becomes the else). */
export function addBranch(doc: Json, edgeIndex: number, target: Target): { doc: Json; branchIndex: number } {
  const out = expandShorthand(doc, edgeIndex);
  const view = edgeAt(out, edgeIndex);
  const to = list(getIn(out, ["edges", edgeIndex, "to"]));
  const k = view === null ? -1 : view.branches.findIndex((b) => b.isElse);
  if (k === -1) return { doc: setIn(out, ["edges", edgeIndex, "to"], [...to, { step: targetString(target) }]), branchIndex: to.length };
  const next = [...to.slice(0, k), { step: targetString(target), when: "" }, ...to.slice(k)];
  return { doc: setIn(out, ["edges", edgeIndex, "to"], next), branchIndex: k };
}

/** `{from, to: "t", with, limits, check, context, ...rest}` -> `{from, to: [{step: "t", with, limits, check, context}],
 *  ...rest}`: `to` keeps its position, the lifted keys move into the branch in that order. */
export function expandShorthand(doc: Json, edgeIndex: number): Json {
  const edge = getIn(doc, ["edges", edgeIndex]);
  if (!isObject(edge) || typeof edge.to !== "string") return doc;
  const branch: JsonObject = { step: edge.to };
  for (const k of LIFTED) if (Object.hasOwn(edge, k)) branch[k] = edge[k] as Json;
  const next: JsonObject = {};
  for (const [k, v] of Object.entries(edge)) {
    if ((LIFTED as readonly string[]).includes(k)) continue;
    next[k] = k === "to" ? [branch] : v;
  }
  return setIn(doc, ["edges", edgeIndex], next);
}

export function updateBranch(doc: Json, e: number, b: number, patch: BranchPatch): Json {
  let out = doc;
  if (patch.when !== undefined || patch.name !== undefined) out = expandShorthand(out, e);
  const view = edgeAt(out, e);
  if (view === null || view.branches[b] === undefined) return doc;
  if (!view.shorthand) out = objectifyBranch(out, e, b);
  if (patch.target !== undefined) out = setIn(out, branchLoc(view, b, "step"), targetString(patch.target));
  for (const key of ["when", "name", "check", "context"] as const) {
    const value = patch[key];
    if (value === undefined) continue;
    if (value === null || view.shorthand) {
      out = setIn(out, branchLoc(view, b, key), value ?? undefined);
      continue;
    }
    const path = ["edges", e, "to", b];
    const item = obj(getIn(out, path));
    out = setIn(out, path, setKeyAfter(item, "step", key, value));
  }
  return out;
}

/** Replaces a branch's unknown keys (the lossless escape hatch); known branch keys in `extra` are ignored. On a
 *  shorthand edge the unknown keys are edge keys. */
export function setBranchExtra(doc: Json, e: number, b: number, extra: Record<string, Json>): Json {
  const view = edgeAt(doc, e);
  const branch = view?.branches[b];
  if (view === null || branch === undefined) return doc;
  const reserved = view.shorthand ? new Set([...EDGE_KEYS, ...LIFTED]) : BRANCH_KEYS;
  let out = view.shorthand ? doc : objectifyBranch(doc, e, b);
  for (const k of Object.keys(branch.extra)) if (!Object.hasOwn(extra, k)) out = setIn(out, branchLoc(view, b, k), undefined);
  for (const [k, v] of Object.entries(extra)) if (!reserved.has(k)) out = setIn(out, branchLoc(view, b, k), v);
  return out;
}

/** Writes via `branchLoc`; deleting the last key deletes the map. */
export function setBranchMapValue(
  doc: Json, e: number, b: number, map: "with" | "limits", key: string, value: Json | undefined,
): Json {
  const view = edgeAt(doc, e);
  if (view === null || view.branches[b] === undefined) return doc;
  const out = view.shorthand ? doc : objectifyBranch(doc, e, b);
  const next = setIn(out, branchLoc(view, b, map, key), value);
  const left = getIn(next, branchLoc(view, b, map));
  if (isObject(left) && Object.keys(left).length === 0) return setIn(next, branchLoc(view, b, map), undefined);
  return next;
}

export function renameBranchMapKey(doc: Json, e: number, b: number, map: "with" | "limits", from: string, to: string): Json {
  const view = edgeAt(doc, e);
  if (view === null || view.branches[b] === undefined) return doc;
  const loc = branchLoc(view, b, map);
  const current = getIn(doc, loc);
  if (!isObject(current)) return doc;
  return setIn(doc, loc, renameKey(current, from, to));
}

export function moveBranch(doc: Json, e: number, from: number, to: number): Json {
  const out = expandShorthand(doc, e);
  const branches = list(getIn(out, ["edges", e, "to"]));
  if (from === to || branches[from] === undefined || to < 0 || to >= branches.length) return out;
  const next = [...branches];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved as Json);
  return setIn(out, ["edges", e, "to"], next);
}

/** Removing the last branch removes the edge. */
export function removeBranch(doc: Json, e: number, b: number): Json {
  const view = edgeAt(doc, e);
  if (view === null || view.branches[b] === undefined) return doc;
  if (view.branches.length === 1) return removeEdge(doc, e);
  return setIn(doc, ["edges", e, "to", b], undefined);
}

export function removeEdge(doc: Json, e: number): Json {
  return setIn(doc, ["edges", e], undefined);
}

/** "deterministic" deletes the key. */
export function setEdgeKind(doc: Json, e: number, kind: string): Json {
  return setIn(doc, ["edges", e, "kind"], kind === "deterministic" ? undefined : kind);
}

export function setEntry(doc: Json, name: string): Json {
  return setIn(doc, ["entry"], name);
}

export function setTop(doc: Json, key: "name" | "goal" | "latency" | "provider" | "on_error", value: Json | undefined): Json {
  return setIn(doc, [key], value);
}

/** env.base; deleting the last env key deletes env. */
export function setBase(doc: Json, base: string | undefined): Json {
  const out = setIn(doc, ["env", "base"], base);
  const env = getIn(out, ["env"]);
  return isObject(env) && Object.keys(env).length === 0 ? setIn(out, ["env"], undefined) : out;
}

/** [] deletes the key; existing `{step, with}` items keep their `with`. */
export function setFinally(doc: Json, steps: string[]): Json {
  if (steps.length === 0) return setIn(doc, ["finally"], undefined);
  const current = list(getIn(doc, ["finally"]));
  const next = steps.map((s) => current.find((item) => targetsStep(item, s)) ?? s);
  return setIn(doc, ["finally"], next);
}

/** Replaces a field mapping; ["outputs", "done"] of a flat (done-only) doc is the `outputs` mapping itself. */
export function setFieldMap(doc: Json, path: ["inputs"] | ["outputs", string], fields: [string, Json][]): Json {
  const map: JsonObject = Object.fromEntries(fields);
  if (path[0] === "outputs" && outputsForm(doc) === "flat") {
    return path[1] === "done" ? setIn(doc, ["outputs"], map) : doc;
  }
  return setIn(doc, path, map);
}

/** Process exits (declared), with the proto-step normalisation rules (PLAN §15 item 2). */
export function processExits(doc: Json): string[] {
  return docExits(doc);
}

/** outputs key; `exits` entry; every "$exit.<from>" target; examples[].exit */
export function renameProcessExit(doc: Json, from: string, to: string): Json {
  const out = renameExit(doc, from, to);
  const old = `${EXIT_PREFIX}${from}`;
  const now = `${EXIT_PREFIX}${to}`;
  return mapEdges(out, (edge) => {
    if (!isObject(edge)) return edge;
    if (edge.to === old) return { ...edge, to: now };
    if (!Array.isArray(edge.to)) return edge;
    let changed = false;
    const branches = edge.to.map((item) => {
      if (item === old) {
        changed = true;
        return now;
      }
      if (isObject(item) && item.step === old) {
        changed = true;
        return { ...item, step: now };
      }
      return item;
    });
    return changed ? { ...edge, to: branches } : edge;
  });
}

export function setExamples(doc: Json, examples: Json[]): Json {
  if (examples.length === 0 && getIn(doc, ["examples"]) === undefined) return doc;
  return setIn(doc, ["examples"], examples);
}

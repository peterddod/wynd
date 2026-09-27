// Process tab: every top-level process.yaml field through processDoc ops (`$DRAFTS/07 §7.8`). Unknown top-level keys
// are kept and editable as JSON; `env.vars` is kept as is.
import type { ReactElement } from "react";
import { useApi, useDesign, useMeta } from "../../api/context";
import type { Json } from "../../api/types";
import { getIn, isObject, setIn } from "../../model/json";
import {
  processExits, renameProcessExit, setBase, setEntry, setExamples, setFieldMap, setFinally, setTop, stepNames,
} from "../../model/processDoc";
import { addExit, outputFields, removeExit, renameField } from "../../model/protoDoc";
import { useQuery } from "../../state/query";
import { ExamplesEditor } from "../examples/ExamplesEditor";
import { isReadOnly, processInterface, useDesignState } from "../graph/designHooks";
import { ExitsEditor } from "../inspector/ExitsEditor";
import { ExtraFieldsEditor } from "../inspector/ExtraFieldsEditor";
import { FieldTableEditor } from "../inspector/FieldTableEditor";

const KNOWN = new Set([
  "kind", "name", "goal", "latency", "provider", "env", "entry", "inputs", "outputs", "exits", "examples", "steps", "edges",
  "on_error", "finally",
]);

function text(v: Json | undefined): string {
  return typeof v === "string" ? v : "";
}

export function ProcessSettings(): ReactElement | null {
  const api = useApi();
  const design = useDesign();
  const meta = useMeta();
  const s = useDesignState();
  const providers = useQuery("providers", () => api.providers.list());
  if (s === null) return null;
  if (s.parseError !== null) {
    return <p className="wg-parse-error" role="alert">process.yaml can't be parsed: {s.parseError.message}</p>;
  }
  const doc = s.process.doc;
  const readOnly = isReadOnly(s);
  const steps = stepNames(doc);
  const exits = processExits(doc);
  const types = meta?.proto_types ?? [];
  const provider = text(getIn(doc, ["provider"]));
  const providerNames = [...new Set([...(providers.data?.providers ?? []).map((p) => p.name), ...(provider === "" ? [] : [provider])])];
  const finallySteps = new Set((Array.isArray(getIn(doc, ["finally"])) ? getIn(doc, ["finally"]) as Json[] : [])
    .map((item) => (typeof item === "string" ? item : text(getIn(item, ["step"])))));
  const extra: Record<string, Json> = {};
  if (isObject(doc)) for (const [k, v] of Object.entries(doc)) if (!KNOWN.has(k)) extra[k] = v;

  function edit(fn: (d: Json) => Json, structural = false): void {
    design.apply("edit process settings", (d) => ({ ...d, process: fn(d.process) }), { structural });
  }

  return (
    <form className="wg-settings" aria-label="Process settings" onSubmit={(e) => e.preventDefault()}>
      <fieldset disabled={readOnly}>
        <label className="wg-field">
          <span>Name</span>
          <input value={text(getIn(doc, ["name"]))} onChange={(e) => edit((d) => setTop(d, "name", e.target.value))} />
        </label>
        <label className="wg-field">
          <span>Goal</span>
          <textarea value={text(getIn(doc, ["goal"]))} rows={2}
                    onChange={(e) => edit((d) => setTop(d, "goal", e.target.value === "" ? undefined : e.target.value))} />
        </label>
        <label className="wg-field">
          <span>Latency</span>
          <select value={text(getIn(doc, ["latency"]))} onChange={(e) => edit((d) => setTop(d, "latency", e.target.value === "" ? undefined : e.target.value))}>
            <option value="">(none)</option>
            {(meta?.latency ?? ["fast", "normal"]).map((l) => <option key={l} value={l}>{l}</option>)}
          </select>
        </label>
        <label className="wg-field">
          <span>Provider</span>
          <select value={provider} onChange={(e) => edit((d) => setTop(d, "provider", e.target.value === "" ? undefined : e.target.value))}>
            <option value="">(workspace default: {meta?.default_provider ?? "claude-code"})</option>
            {providerNames.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className="wg-field">
          <span>Base image</span>
          <select value={text(getIn(doc, ["env", "base"])) || "debian-slim-python"}
                  onChange={(e) => edit((d) => setBase(d, e.target.value))}>
            {(meta?.bases ?? ["debian-slim-python", "alpine-python"]).map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </label>
        <label className="wg-field">
          <span>Entry step</span>
          <select value={text(getIn(doc, ["entry"]))} onChange={(e) => edit((d) => setEntry(d, e.target.value))}>
            {text(getIn(doc, ["entry"])) === "" && <option value="">(none)</option>}
            {steps.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
      </fieldset>

      <h3>Inputs</h3>
      <FieldTableEditor rows={isObject(getIn(doc, ["inputs"])) ? Object.entries(getIn(doc, ["inputs"]) as Record<string, Json>) : []}
                        typeOptions={types} readOnly={readOnly}
                        onChange={(rows) => edit((d) => setFieldMap(d, ["inputs"], rows))}
                        onRename={(from, to) => edit((d) => renameField(d, ["inputs"], from, to))} />

      <h3>Outputs by exit</h3>
      <ExitsEditor exits={exits} fields={(x) => outputFields(doc, x)} typeOptions={types} readOnly={readOnly}
                   onFieldsChange={(x, rows) => edit((d) => setFieldMap(d, ["outputs", x], rows))}
                   onFieldRename={(x, from, to) => edit((d) => renameField(d, ["outputs", x], from, to))}
                   onAdd={(x) => edit((d) => addExit(d, x), true)}
                   onRename={(from, to) => edit((d) => renameProcessExit(d, from, to), true)}
                   onRemove={(x) => edit((d) => removeExit(d, x), true)} />

      <fieldset disabled={readOnly}>
        <label className="wg-field">
          <span>On error</span>
          <select value={text(getIn(doc, ["on_error"]))} onChange={(e) => edit((d) => setTop(d, "on_error", e.target.value === "" ? undefined : e.target.value))}>
            <option value="">(default handler)</option>
            {steps.map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
        <fieldset className="wg-finally">
          <legend>Finally</legend>
          {steps.map((k) => (
            <label key={k} className="wg-check">
              <input type="checkbox" checked={finallySteps.has(k)}
                     onChange={(e) => {
                       const next = steps.filter((x) => (x === k ? e.target.checked : finallySteps.has(x)));
                       edit((d) => setFinally(d, next));
                     }} />
              {k}
            </label>
          ))}
        </fieldset>
      </fieldset>

      <ExamplesEditor label="Process examples" examples={Array.isArray(getIn(doc, ["examples"])) ? getIn(doc, ["examples"]) as Json[] : []}
                      exits={exits} iface={processInterface(s.iface)} readOnly={readOnly}
                      onChange={(next) => edit((d) => setExamples(d, next))} />

      <ExtraFieldsEditor label="Other fields" value={extra} readOnly={readOnly}
                         onChange={(next) => edit((d) => {
                           let out = d;
                           for (const k of Object.keys(extra)) if (!Object.hasOwn(next, k)) out = setIn(out, [k], undefined);
                           for (const [k, v] of Object.entries(next)) if (!KNOWN.has(k)) out = setIn(out, [k], v);
                           return out;
                         })} />
    </form>
  );
}

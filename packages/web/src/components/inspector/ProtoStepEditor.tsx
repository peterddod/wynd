// Proto-step editor: instruction, plain-language summary, examples, schema (advanced), env (`$DRAFTS/07 §7.9`).
// Examples come before the schema tables: users edit examples, the compiler infers schemas. Renaming an exit also
// renames `from: "<k>.<old>"` in the open process for every step key using this proto (one apply, two files);
// other processes sharing the proto are not touched (the shared banner says so).
import { useState, type ReactElement } from "react";
import { useDesign, useMeta } from "../../api/context";
import type { Json, JsonObject } from "../../api/types";
import { getIn, isObject, setIn } from "../../model/json";
import { edgeIndexFor, setFieldMap } from "../../model/processDoc";
import {
  addExit, exits as protoExits, outputFields, removeExit, renameExit, renameField, setEnv, setExamples, setExitCode,
  setInstruction,
} from "../../model/protoDoc";
import { isDirty } from "../../state/design";
import { ExamplesEditor } from "../examples/ExamplesEditor";
import { isReadOnly, stepsByProto, useDesignState } from "../graph/designHooks";
import { ExitsEditor } from "./ExitsEditor";
import { ExtraFieldsEditor } from "./ExtraFieldsEditor";
import { FieldTableEditor } from "./FieldTableEditor";
import { SchemaSummary } from "./SchemaSummary";

export interface ProtoStepEditorProps {
  step: string;                      // step key in the open process
  protoPath: string;                 // workspace-relative proto file
}

const KNOWN = new Set(["kind", "name", "instruction", "inputs", "outputs", "exits", "exit_codes", "examples", "env"]);

export function ProtoStepEditor({ step, protoPath }: ProtoStepEditorProps): ReactElement | null {
  const design = useDesign();
  const meta = useMeta();
  const s = useDesignState();
  const [codes, setCodes] = useState(false);
  const [newCode, setNewCode] = useState("");
  if (s === null) return null;
  const file = s.protos[protoPath];
  if (file === undefined || file.deleted === true) return <p className="wg-muted">The proto-step file is not loaded.</p>;
  const doc = file.doc;
  const info = s.steps[step];
  const readOnly = isReadOnly(s);
  const name = String(getIn(doc, ["name"]) ?? step);
  const label = `edit proto ${name}`;
  const exits = protoExits(doc);
  const types = meta?.proto_types ?? [];
  const others = (info?.used_by ?? []).filter((p) => p !== s.processId);
  const shared = info?.ref_kind === "root" || others.length > 0;
  const exitCodes = getIn(doc, ["exit_codes"]);
  const env = getIn(doc, ["env"]);
  const deps = getIn(env, ["deps"]);
  const requires = getIn(env, ["requires"]);
  const examples = getIn(doc, ["examples"]);
  const keys = stepsByProto(s)[protoPath] ?? [step];
  const extra: Record<string, Json> = {};
  if (isObject(doc)) for (const [k, v] of Object.entries(doc)) if (!KNOWN.has(k)) extra[k] = v;

  function edit(fn: (d: Json) => Json, structural = false): void {
    design.apply(label, (d) => ({ ...d, protos: { ...d.protos, [protoPath]: fn(d.protos[protoPath] ?? doc) } }), { structural });
  }

  function renameExitEverywhere(from: string, to: string): void {
    design.apply(`rename exit ${name}.${from} → ${to}`, (d) => {
      let process = d.process;
      for (const k of keys) {
        const e = edgeIndexFor(process, k, from);
        if (e !== -1) process = setIn(process, ["edges", e, "from"], `${k}.${to}`);
      }
      return { process, protos: { ...d.protos, [protoPath]: renameExit(d.protos[protoPath] ?? doc, from, to) } };
    }, { structural: true });
  }

  return (
    <section className="wg-proto-editor" aria-label="Proto-step">
      <h3>Proto-step <span className="wg-mono wg-muted">{protoPath}</span></h3>
      {shared && (
        <p className="wg-banner wg-banner-shared" role="note">
          Shared step <span className="wg-mono">{info?.use ?? name}</span>
          {others.length > 0 ? `, also used by ${others.join(", ")}` : ""}. Edits affect those processes.
        </p>
      )}
      <label className="wg-field">
        <span>Instruction</span>
        <textarea value={typeof getIn(doc, ["instruction"]) === "string" ? getIn(doc, ["instruction"]) as string : ""}
                  rows={5} readOnly={readOnly} onChange={(e) => edit((d) => setInstruction(d, e.target.value))} />
      </label>
      <section aria-label="What this step does">
        <h4>What this step does (plain language)</h4>
        <SchemaSummary iface={info?.interface ?? null} updating={isDirty(file)} />
      </section>
      <ExamplesEditor label="Examples" examples={Array.isArray(examples) ? examples : []} exits={exits}
                      iface={info?.interface ?? null} readOnly={readOnly}
                      onChange={(next) => edit((d) => setExamples(d, next))} />
      <details className="wg-schema-advanced">
        <summary>Schema (advanced)</summary>
        <h4>Inputs</h4>
        <FieldTableEditor rows={isObject(getIn(doc, ["inputs"])) ? Object.entries(getIn(doc, ["inputs"]) as JsonObject) : []}
                          typeOptions={types} readOnly={readOnly}
                          onChange={(rows) => edit((d) => setFieldMap(d, ["inputs"], rows))}
                          onRename={(from, to) => edit((d) => renameField(d, ["inputs"], from, to))} />
        <h4>Outputs by exit</h4>
        <ExitsEditor exits={exits} fields={(x) => outputFields(doc, x)} typeOptions={types} readOnly={readOnly}
                     onFieldsChange={(x, rows) => edit((d) => setFieldMap(d, ["outputs", x], rows))}
                     onFieldRename={(x, from, to) => edit((d) => renameField(d, ["outputs", x], from, to))}
                     onAdd={(x) => edit((d) => addExit(d, x))}
                     onRename={renameExitEverywhere}
                     onRemove={(x) => edit((d) => removeExit(d, x), true)} />
        {isObject(exitCodes) || codes ? (
          <fieldset className="wg-exit-codes">
            <legend>Exit codes (shell)</legend>
            <ul>
              {Object.entries(isObject(exitCodes) ? exitCodes : {}).map(([code, exit]) => (
                <li key={code} className="wg-row">
                  <span className="wg-mono">{code}</span>
                  <select aria-label={`Exit for code ${code}`} value={String(exit)} disabled={readOnly}
                          onChange={(e) => edit((d) => setExitCode(d, code, e.target.value))}>
                    {[...exits, "error"].map((x) => <option key={x} value={x}>{x}</option>)}
                  </select>
                  {!readOnly && <button type="button" aria-label={`Remove code ${code}`} onClick={() => edit((d) => setExitCode(d, code, undefined))}>×</button>}
                </li>
              ))}
            </ul>
            {!readOnly && (
              <div className="wg-row">
                <input aria-label="Exit code" placeholder="0-255 or *" value={newCode} onChange={(e) => setNewCode(e.target.value)} />
                <button type="button" disabled={!/^(\*|\d{1,3})$/.test(newCode)}
                        onClick={() => { edit((d) => setExitCode(d, newCode, newCode === "0" ? "done" : "error")); setNewCode(""); }}>
                  Add code
                </button>
              </div>
            )}
          </fieldset>
        ) : (
          !readOnly && <button type="button" onClick={() => setCodes(true)}>Map shell exit codes</button>
        )}
        <fieldset className="wg-env">
          <legend>Env</legend>
          <label className="wg-field">
            <span>Dependencies (one requirement per line)</span>
            <textarea className="wg-mono" readOnly={readOnly}
                      defaultValue={Array.isArray(deps) ? deps.filter((x) => typeof x === "string").join("\n") : ""}
                      onBlur={(e) => {
                        const next = e.target.value.split("\n").map((l) => l.trim()).filter((l) => l !== "");
                        const current = Array.isArray(deps) ? deps : [];
                        if (JSON.stringify(next) !== JSON.stringify(current)) edit((d) => setEnv(d, { deps: next }));
                      }} />
          </label>
          <label className="wg-field">
            <span>Requires</span>
            <select value={typeof requires === "string" ? requires : ""} disabled={readOnly}
                    onChange={(e) => edit((d) => setEnv(d, { requires: e.target.value === "" ? null : e.target.value }))}>
              <option value="">(none)</option>
              <option value="glibc">glibc</option>
            </select>
          </label>
        </fieldset>
      </details>
      <ExtraFieldsEditor label="Other fields" value={extra} readOnly={readOnly}
                         onChange={(next) => edit((d) => {
                           let out = d;
                           for (const k of Object.keys(extra)) if (!Object.hasOwn(next, k)) out = setIn(out, [k], undefined);
                           for (const [k, v] of Object.entries(next)) if (!KNOWN.has(k)) out = setIn(out, [k], v);
                           return out;
                         })} />
    </section>
  );
}

// `with:` rows per target input field (`$DRAFTS/07 §7.7`). Each value is an expression bound at
// ["edges", e, "to", b, "with", field]; an empty input deletes the key. A non-string YAML literal (a number or a
// mapping) is shown as JSON text and stays a literal when the edited text still parses as one. Keys that are not an
// input of the target are flagged; an unknown target interface falls back to free key rows.
import { useState, type ReactElement } from "react";
import { useDesign } from "../../api/context";
import type { Json, JsonSchema } from "../../api/types";
import { branchKey, edgeAt, normalizedLoc, setBranchMapValue, targetString } from "../../model/processDoc";
import { describe } from "../../model/schemaText";
import { formatLoose } from "../../model/values";
import { useDesignState } from "../graph/designHooks";
import { ExpressionInput } from "./ExpressionInput";

export interface FieldRow {
  name: string;
  schema: JsonSchema | null;
  required: boolean;
}

export interface WithMappingEditorProps {
  e: number;
  b: number;
  targetFields: FieldRow[] | null;   // null: unknown interface -> free key rows
  readOnly?: boolean;
}

/** The value to write for edited text: literals stay literals, everything else is an expression string. */
export function withValue(text: string, previous: Json | undefined): Json | undefined {
  if (text === "") return undefined;
  if (previous !== undefined && typeof previous !== "string") {
    try {
      const parsed = JSON.parse(text) as Json;
      if (typeof parsed !== "string") return parsed;
    } catch {
      // not a literal any more: an expression
    }
  }
  return text;
}

export function WithMappingEditor({ e, b, targetFields, readOnly = false }: WithMappingEditorProps): ReactElement | null {
  const design = useDesign();
  const s = useDesignState();
  const [newKey, setNewKey] = useState("");
  const view = s === null ? null : edgeAt(s.process.doc, e);
  const branch = view?.branches[b];
  if (view === null || view === undefined || branch === undefined) return null;
  const values = branch.with;
  const target = targetString(branch.target);
  const label = `edit branch ${branchKey(view, b)}`;

  function write(key: string, value: Json | undefined): void {
    design.apply(label, (d) => ({ ...d, process: setBranchMapValue(d.process, e, b, "with", key, value) }));
  }

  function row(key: string, text: string, flagged: boolean): ReactElement {
    return (
      <li key={key} className="wg-with-row">
        <ExpressionInput id={`wg-with-${e}-${b}-${key}`} label={text} value={values[key] === undefined ? "" : formatLoose(values[key] as Json)}
                         loc={normalizedLoc(e, b, "with", key)} readOnly={readOnly}
                         onChange={(t) => write(key, withValue(t, values[key]))} />
        {flagged && <span className="wg-flag">not an input of {target}</span>}
        {(flagged || targetFields === null) && !readOnly && values[key] !== undefined && (
          <button type="button" aria-label={`Remove mapping ${key}`} onClick={() => write(key, undefined)}>Remove</button>
        )}
      </li>
    );
  }

  const known = new Set((targetFields ?? []).map((f) => f.name));
  const extra = Object.keys(values).filter((k) => !known.has(k));
  return (
    <fieldset className="wg-with">
      <legend>With (inputs of {target})</legend>
      <ul>
        {(targetFields ?? []).map((f) => row(f.name, `${f.name} — ${describe(f.schema)}${f.required ? " (required)" : ""}`, false))}
        {extra.map((k) => row(k, k, targetFields !== null))}
      </ul>
      {targetFields === null && !readOnly && (
        <div className="wg-row">
          <input aria-label="New mapping field" placeholder="field" value={newKey} onChange={(ev) => setNewKey(ev.target.value)} />
          <button type="button" disabled={newKey === "" || values[newKey] !== undefined}
                  onClick={() => { write(newKey, "null"); setNewKey(""); }}>+ Add mapping</button>
        </div>
      )}
    </fieldset>
  );
}

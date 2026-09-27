// Exits with per-exit field tables: add, rename, remove (`$DRAFTS/07 §7.8`, §7.9). `error` is implicit and never
// declared.
import { useState, type ReactElement } from "react";
import type { Json } from "../../api/types";
import { FieldTableEditor } from "./FieldTableEditor";

export interface ExitsEditorProps {
  exits: string[];
  fields(exit: string): [string, Json][];
  onFieldsChange(exit: string, rows: [string, Json][]): void;
  onFieldRename(exit: string, from: string, to: string): void;
  onAdd(name: string): void;
  onRename(from: string, to: string): void;
  onRemove(name: string): void;
  typeOptions: string[];
  readOnly?: boolean;
}

const EXIT = /^[a-z_][a-z0-9_]*$/;

export function exitNameProblem(name: string, exits: string[]): string | null {
  if (!EXIT.test(name)) return "Use lower-case letters, digits and _.";
  if (name === "error") return "error is implicit on every step and process.";
  if (exits.includes(name)) return `Exit ${name} already exists.`;
  return null;
}

export function ExitsEditor(props: ExitsEditorProps): ReactElement | null {
  const { exits, fields, onFieldsChange, onFieldRename, onAdd, onRename, onRemove, typeOptions, readOnly = false } = props;
  const [name, setName] = useState("");
  const problem = name === "" ? null : exitNameProblem(name, exits);
  return (
    <div className="wg-exits-editor">
      {exits.map((exit) => (
        <fieldset key={exit} className="wg-exit-fields">
          <legend>
            <input aria-label={`Exit ${exit} name`} defaultValue={exit} readOnly={readOnly}
                   onBlur={(e) => {
                     const to = e.target.value.trim();
                     if (to !== exit && exitNameProblem(to, exits) === null) onRename(exit, to);
                     else e.target.value = exit;
                   }} />
            {!readOnly && exits.length > 1 && (
              <button type="button" aria-label={`Remove exit ${exit}`} onClick={() => onRemove(exit)}>Remove exit</button>
            )}
          </legend>
          <FieldTableEditor rows={fields(exit)} typeOptions={typeOptions} readOnly={readOnly}
                            onChange={(rows) => onFieldsChange(exit, rows)} onRename={(from, to) => onFieldRename(exit, from, to)} />
        </fieldset>
      ))}
      {!readOnly && (
        <div className="wg-row">
          <input aria-label="New exit name" placeholder="new exit" value={name} onChange={(e) => setName(e.target.value)}
                 aria-invalid={problem !== null} />
          <button type="button" disabled={name === "" || problem !== null} onClick={() => { onAdd(name); setName(""); }}>
            Add exit
          </button>
          {problem !== null && <span className="wg-error-text">{problem}</span>}
        </div>
      )}
    </div>
  );
}

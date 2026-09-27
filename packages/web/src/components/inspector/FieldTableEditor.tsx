// Field name/type rows with a type <datalist> (`$DRAFTS/07 §7.9`). A rename is applied when the name input loses
// focus; a non-string type (an inline mapping or `[T]`) is edited as JSON text and parsed on blur.
import { useEffect, useId, useState, type ReactElement } from "react";
import type { Json } from "../../api/types";

export interface FieldTableEditorProps {
  rows: [string, Json][];
  onChange(rows: [string, Json][]): void;
  onRename(from: string, to: string): void;
  typeOptions: string[];
  readOnly?: boolean;
}

const FIELD = /^[A-Za-z_][A-Za-z0-9_]*$/;

function freshName(rows: [string, Json][]): string {
  const names = new Set(rows.map(([n]) => n));
  if (!names.has("field")) return "field";
  let i = 2;
  while (names.has(`field_${i}`)) i += 1;
  return `field_${i}`;
}

export function FieldTableEditor({ rows, onChange, onRename, typeOptions, readOnly = false }: FieldTableEditorProps): ReactElement | null {
  const listId = useId();
  const names = rows.map(([n]) => n);
  return (
    <div className="wg-field-table">
      <table>
        <thead><tr><th scope="col">Field</th><th scope="col">Type</th><th><span className="wg-visually-hidden">Actions</span></th></tr></thead>
        <tbody>
          {rows.map(([name, type], i) => (
            <FieldRow key={name} name={name} type={type} listId={listId} readOnly={readOnly} taken={names}
                      onRename={(to) => onRename(name, to)}
                      onType={(t) => onChange(rows.map((r, j) => (j === i ? [r[0], t] : r)))}
                      onRemove={() => onChange(rows.filter((_, j) => j !== i))} />
          ))}
        </tbody>
      </table>
      <datalist id={listId}>{typeOptions.map((t) => <option key={t} value={t} />)}</datalist>
      {!readOnly && <button type="button" onClick={() => onChange([...rows, [freshName(rows), "string"]])}>+ Add field</button>}
    </div>
  );
}

interface FieldRowProps {
  name: string;
  type: Json;
  listId: string;
  readOnly: boolean;
  taken: string[];
  onRename(to: string): void;
  onType(type: Json): void;
  onRemove(): void;
}

function FieldRow({ name, type, listId, readOnly, taken, onRename, onType, onRemove }: FieldRowProps): ReactElement {
  const [draft, setDraft] = useState(name);
  const serialised = typeof type === "string" ? type : JSON.stringify(type);
  const [json, setJson] = useState(serialised);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setJson(serialised), [serialised]);

  function commitName(): void {
    const to = draft.trim();
    if (to === name) return;
    if (!FIELD.test(to) || taken.includes(to)) {
      setDraft(name);
      return;
    }
    onRename(to);
  }

  function commitJson(): void {
    try {
      const parsed = JSON.parse(json) as Json;
      setError(null);
      onType(parsed);
    } catch (e) {
      setError(`Invalid JSON: ${(e as Error).message}`);
    }
  }

  return (
    <tr>
      <td>
        <input aria-label={`Field name ${name}`} value={draft} readOnly={readOnly} onChange={(e) => setDraft(e.target.value)}
               onBlur={commitName} />
      </td>
      <td>
        {typeof type === "string" ? (
          <input aria-label={`Type of ${name}`} list={listId} value={type} readOnly={readOnly}
                 onChange={(e) => onType(e.target.value)} />
        ) : (
          <>
            <textarea aria-label={`Type of ${name} (JSON)`} className="wg-mono" value={json} readOnly={readOnly}
                      aria-invalid={error !== null} onChange={(e) => setJson(e.target.value)} onBlur={commitJson} />
            {error !== null && <span className="wg-error-text">{error}</span>}
          </>
        )}
      </td>
      <td>
        {!readOnly && <button type="button" aria-label={`Remove field ${name}`} onClick={onRemove}>×</button>}
      </td>
    </tr>
  );
}

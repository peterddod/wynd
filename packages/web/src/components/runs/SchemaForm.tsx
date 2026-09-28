// One ValueInput per schema property, in order, with required marks; path fields offer Upload (inside ValueInput)
// when `allowUpload` (`$DRAFTS/07 §11.3`). Without properties it falls back to free key/value rows.
import { useState, type ReactElement } from "react";
import type { Json, JsonSchema } from "../../api/types";
import { ValueInput } from "../examples/ValueInput";

export interface SchemaFormProps {
  schema: JsonSchema | null;
  value: Record<string, Json>;
  onChange(v: Record<string, Json>): void;
  allowUpload?: boolean;
}

export function SchemaForm({ schema, value, onChange, allowUpload = false }: SchemaFormProps): ReactElement {
  const properties = schema?.properties;
  if (properties === undefined) return <FreeRows value={value} onChange={onChange} />;
  const names = Object.keys(properties);
  if (names.length === 0) return <p className="wo-muted">No inputs.</p>;
  const required = new Set(schema?.required ?? []);

  function set(name: string, v: Json | undefined): void {
    const next = { ...value };
    if (v === undefined) delete next[name];
    else next[name] = v;
    onChange(next);
  }

  return (
    <div className="wo-schema-form">
      {names.map((name) => (
        <ValueInput
          key={name}
          label={name}
          schema={properties[name] ?? null}
          value={value[name]}
          onChange={(v) => set(name, v)}
          allowUpload={allowUpload}
          required={required.has(name)}
        />
      ))}
    </div>
  );
}

/** Fields named by `schema.required` that have no value yet. */
export function missingRequired(schema: JsonSchema | null, value: Record<string, Json>): string[] {
  return (schema?.required ?? []).filter((name) => value[name] === undefined || value[name] === null || value[name] === "");
}

interface Row {
  key: string;
  value: Json | undefined;
}

function FreeRows({ value, onChange }: { value: Record<string, Json>; onChange(v: Record<string, Json>): void }): ReactElement {
  const [rows, setRows] = useState<Row[]>(() => Object.entries(value).map(([key, v]) => ({ key, value: v })));

  function update(next: Row[]): void {
    setRows(next);
    const out: Record<string, Json> = {};
    for (const row of next) {
      if (row.key !== "" && row.value !== undefined) out[row.key] = row.value;
    }
    onChange(out);
  }

  return (
    <div className="wo-schema-form">
      {rows.map((row, i) => (
        <div key={i} className="wo-free-row">
          <input
            aria-label={`Field ${i + 1} name`}
            value={row.key}
            onChange={(e) => update(rows.map((r, j) => (j === i ? { ...r, key: e.target.value.trim() } : r)))}
          />
          <ValueInput
            label={row.key === "" ? `Field ${i + 1} value` : row.key}
            schema={null}
            value={row.value}
            onChange={(v) => update(rows.map((r, j) => (j === i ? { ...r, value: v } : r)))}
          />
          <button type="button" aria-label={`Remove field ${i + 1}`} onClick={() => update(rows.filter((_, j) => j !== i))}>
            Remove
          </button>
        </div>
      ))}
      <button type="button" onClick={() => setRows([...rows, { key: "", value: undefined }])}>Add field</button>
    </div>
  );
}

// A typed value control chosen from the field's schema (`$DRAFTS/07 §7.11`; path fields have `format === "path"`).
// Reused by WEB-OPS' SchemaForm. Structured values are edited as JSON text parsed on blur; invalid JSON shows an error
// and is not propagated. Clearing a field gives `undefined`.
import { useEffect, useId, useRef, useState, type ReactElement } from "react";
import { useApi } from "../../api/context";
import type { Json, JsonSchema } from "../../api/types";
import { resolveSchema } from "../../model/schemaText";
import { formatLoose, parseLoose } from "../../model/values";

export interface ValueInputProps {
  label: string;
  schema: JsonSchema | null;
  value: Json | undefined;
  onChange(v: Json | undefined): void;   // undefined: the field is cleared
  allowUpload?: boolean;             // path fields: Upload button -> POST /api/uploads
  required?: boolean;
}

type Control = "loose" | "date" | "path" | "text" | "number" | "integer" | "boolean" | "json";

/** The schema's single non-null alternative (`T | null` -> T); `$ref`s resolved against its own `$defs`. */
function effective(schema: JsonSchema | null): JsonSchema | null {
  const s = resolveSchema(schema, schema?.$defs);
  if (s === null) return null;
  const parts = s.anyOf ?? s.oneOf ?? (Array.isArray(s.type) ? s.type.map((t) => ({ type: t })) : undefined);
  if (parts === undefined) return s;
  const rest = parts.filter((p) => p.type !== "null");
  return rest.length === 1 ? resolveSchema(rest[0], schema?.$defs) : { anyOf: rest };
}

export function controlFor(schema: JsonSchema | null): Control {
  const s = effective(schema);
  if (s === null) return "loose";
  if (s.format === "date") return "date";
  if (s.format === "path") return "path";
  switch (s.type) {
    case "string":
      return "text";
    case "number":
      return "number";
    case "integer":
      return "integer";
    case "boolean":
      return "boolean";
  }
  return "json";
}

export function ValueInput({ label, schema, value, onChange, allowUpload = false, required = false }: ValueInputProps): ReactElement | null {
  const id = useId();
  const control = controlFor(schema);
  const caption = <label htmlFor={id}>{label}{required && <span aria-hidden="true"> *</span>}</label>;
  switch (control) {
    case "date":
      return (
        <div className="wg-value">
          {caption}
          <input id={id} type="date" required={required} value={typeof value === "string" ? value : ""}
                 onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)} />
        </div>
      );
    case "number":
    case "integer":
      return (
        <div className="wg-value">
          {caption}
          <input id={id} type="number" step={control === "integer" ? 1 : "any"} required={required}
                 value={typeof value === "number" ? String(value) : ""}
                 onChange={(e) => {
                   if (e.target.value === "") return onChange(undefined);
                   const n = Number(e.target.value);
                   if (Number.isFinite(n)) onChange(n);
                 }} />
        </div>
      );
    case "boolean":
      return (
        <div className="wg-value wg-check">
          <input id={id} type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} />
          {caption}
        </div>
      );
    case "text":
    case "path":
      return <TextValue id={id} caption={caption} value={value} onChange={onChange} required={required}
                        upload={control === "path" && allowUpload} />;
    case "loose":
      return (
        <div className="wg-value">
          {caption}
          <input id={id} className="wg-mono" value={value === undefined ? "" : formatLoose(value)} required={required}
                 onChange={(e) => onChange(e.target.value === "" ? undefined : parseLoose(e.target.value))} />
        </div>
      );
    case "json":
      return <JsonValue id={id} caption={caption} value={value} onChange={onChange} />;
  }
}

function TextValue({ id, caption, value, onChange, required, upload }: {
  id: string; caption: ReactElement; value: Json | undefined; onChange(v: Json | undefined): void; required: boolean; upload: boolean;
}): ReactElement {
  const api = useApi();
  const text = value === undefined ? "" : typeof value === "string" ? value : formatLoose(value);
  const [multi, setMulti] = useState(text.includes("\n") || text.length > 80);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const change = (t: string): void => onChange(t === "" ? undefined : t);

  async function uploadFile(f: File): Promise<void> {
    setUploading(true);
    setError(null);
    try {
      onChange((await api.uploads.put(f)).path);
    } catch (e) {
      setError(`Upload failed: ${(e as Error).message}`);
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="wg-value">
      {caption}
      {multi
        ? <textarea id={id} value={text} required={required} onChange={(e) => change(e.target.value)} rows={4} />
        : <input id={id} value={text} required={required} onChange={(e) => change(e.target.value)} />}
      <button type="button" className="wg-link" onClick={() => setMulti(!multi)}>{multi ? "Single line" : "Multi-line"}</button>
      {upload && (
        <>
          <input ref={file} type="file" hidden aria-label="File to upload"
                 onChange={(e) => { const f = e.target.files?.[0]; if (f !== undefined) void uploadFile(f); }} />
          <button type="button" disabled={uploading} onClick={() => file.current?.click()}>{uploading ? "Uploading…" : "Upload"}</button>
        </>
      )}
      {error !== null && <span className="wg-error-text" role="alert">{error}</span>}
    </div>
  );
}

function JsonValue({ id, caption, value, onChange }: {
  id: string; caption: ReactElement; value: Json | undefined; onChange(v: Json | undefined): void;
}): ReactElement {
  const serialised = value === undefined ? "" : JSON.stringify(value, null, 2);
  const [text, setText] = useState(serialised);
  const [error, setError] = useState<string | null>(null);
  const editing = useRef(false);
  useEffect(() => {
    if (!editing.current) setText(serialised);
  }, [serialised]);

  function commit(): void {
    editing.current = false;
    if (text.trim() === "") {
      setError(null);
      onChange(undefined);
      return;
    }
    try {
      const parsed = JSON.parse(text) as Json;
      setError(null);
      onChange(parsed);
    } catch (e) {
      setError(`Invalid JSON: ${(e as Error).message}`);
    }
  }

  return (
    <div className="wg-value">
      {caption}
      <textarea id={id} className="wg-mono" value={text} rows={Math.min(8, text.split("\n").length + 1)}
                aria-invalid={error !== null} aria-describedby={error === null ? undefined : `${id}-error`}
                onFocus={() => { editing.current = true; }} onChange={(e) => setText(e.target.value)} onBlur={commit} />
      {error !== null && <span id={`${id}-error`} className="wg-error-text" role="alert">{error}</span>}
    </div>
  );
}

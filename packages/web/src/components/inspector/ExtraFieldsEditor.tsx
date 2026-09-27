// Unknown keys as a JSON textarea parsed on blur: the lossless escape hatch (`$DRAFTS/07 §7.7`). Invalid JSON (or a
// non-object) shows an error and is not applied.
import { useEffect, useId, useState, type ReactElement } from "react";
import type { Json } from "../../api/types";
import { isObject } from "../../model/json";

export interface ExtraFieldsEditorProps {
  label: string;
  value: Record<string, Json>;
  onChange(next: Record<string, Json>): void;
  readOnly?: boolean;
}

function pretty(value: Record<string, Json>): string {
  return JSON.stringify(value, null, 2);
}

export function ExtraFieldsEditor({ label, value, onChange, readOnly = false }: ExtraFieldsEditorProps): ReactElement | null {
  const id = useId();
  const serialised = pretty(value);
  const [text, setText] = useState(serialised);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setText(serialised);
    setError(null);
  }, [serialised]);

  function commit(): void {
    let parsed: unknown;
    try {
      parsed = JSON.parse(text === "" ? "{}" : text);
    } catch (e) {
      setError(`Invalid JSON: ${(e as Error).message}`);
      return;
    }
    if (!isObject(parsed as Json)) {
      setError("Enter a JSON object of field names to values.");
      return;
    }
    setError(null);
    if (JSON.stringify(parsed) !== JSON.stringify(value)) onChange(parsed as Record<string, Json>);
  }

  const count = Object.keys(value).length;
  return (
    <details className="wg-extra" open={count > 0 ? true : undefined}>
      <summary>{label}{count > 0 ? ` (${count})` : ""}</summary>
      <label htmlFor={id} className="wg-visually-hidden">{label} (JSON)</label>
      <textarea id={id} className="wg-mono" value={text} readOnly={readOnly} rows={Math.min(10, text.split("\n").length + 1)}
                aria-invalid={error !== null} onChange={(e) => setText(e.target.value)} onBlur={commit} />
      {error !== null && <p className="wg-error-text" role="alert">{error}</p>}
    </details>
  );
}

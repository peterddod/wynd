// manual | schedule (cron, timezone, fixed inputs) | webhook (secret env) (`$DRAFTS/07 §10`). The client only checks
// that a cron expression has five fields; the controller validates it.
import { useId, type ReactElement } from "react";
import type { JsonSchema, Trigger, TriggerKind } from "../../api/types";
import { SchemaForm } from "../runs/SchemaForm";

export interface TriggerEditorProps {
  value: Trigger;
  onChange(next: Trigger): void;
  inputsSchema: JsonSchema | null;   // for a schedule's fixed inputs
}

const KINDS: { kind: TriggerKind; label: string }[] = [
  { kind: "manual", label: "Manual" },
  { kind: "schedule", label: "Schedule" },
  { kind: "webhook", label: "Webhook" },
];

export function cronLooksValid(cron: string): boolean {
  return cron.trim().split(/\s+/).filter((f) => f !== "").length === 5;
}

/** A schedule needs a five-field cron; manual and webhook triggers are always complete. */
export function triggerComplete(t: Trigger): boolean {
  return t.kind !== "schedule" || cronLooksValid(t.cron);
}

function emptyTrigger(kind: TriggerKind): Trigger {
  switch (kind) {
    case "manual":
      return { kind: "manual" };
    case "schedule":
      return { kind: "schedule", cron: "", timezone: null, inputs: {} };
    case "webhook":
      return { kind: "webhook", secret_env: null };
  }
}

export function TriggerEditor({ value, onChange, inputsSchema }: TriggerEditorProps): ReactElement {
  const group = useId();
  const cronHint = useId();
  return (
    <fieldset className="wo-trigger">
      <legend>Trigger</legend>
      <div className="wo-radios">
        {KINDS.map(({ kind, label }) => (
          <label key={kind}>
            <input
              type="radio"
              name={group}
              value={kind}
              checked={value.kind === kind}
              onChange={() => { if (value.kind !== kind) onChange(emptyTrigger(kind)); }}
            />
            {label}
          </label>
        ))}
      </div>
      {value.kind === "manual" && <p className="wo-muted">Runs only when started from Wynd (every release can also be run manually).</p>}
      {value.kind === "schedule" && (
        <>
          <label>
            Cron
            <input
              value={value.cron}
              placeholder="0 7 * * 1-5"
              aria-invalid={value.cron.trim() !== "" && !cronLooksValid(value.cron)}
              aria-describedby={cronHint}
              onChange={(e) => onChange({ ...value, cron: e.target.value })}
            />
          </label>
          <p id={cronHint} className="wo-muted">Five fields: minute, hour, day of month, month, day of week.</p>
          <label>
            Timezone
            <input
              value={value.timezone ?? ""}
              placeholder="UTC"
              onChange={(e) => onChange({ ...value, timezone: e.target.value.trim() === "" ? null : e.target.value.trim() })}
            />
          </label>
          <fieldset className="wo-fixed-inputs">
            <legend>Inputs for each scheduled run</legend>
            <SchemaForm schema={inputsSchema} value={value.inputs} onChange={(inputs) => onChange({ ...value, inputs })} />
          </fieldset>
        </>
      )}
      {value.kind === "webhook" && (
        <>
          <label>
            Secret env var
            <input
              value={value.secret_env ?? ""}
              placeholder="optional, e.g. INVOICE_HOOK_SECRET"
              onChange={(e) => onChange({ ...value, secret_env: e.target.value.trim() === "" ? null : e.target.value.trim() })}
            />
          </label>
          <p className="wo-muted">The webhook body is the process inputs as JSON. Without a secret env var the API token is required.</p>
        </>
      )}
    </fieldset>
  );
}

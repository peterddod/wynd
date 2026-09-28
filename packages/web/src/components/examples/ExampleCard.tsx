// One example: sentence title, exit, typed inputs/outputs forms (`$DRAFTS/07 §7.11`). Also used read-only and for
// corrections by the compile job's ProposalCard. Values not in the schema are flagged and removable (never dropped
// silently); without a schema, free key/value rows parse values loosely.
import { useState, type ReactElement } from "react";
import type { Interface, Json, JsonObject, JsonSchema } from "../../api/types";
import { isObject, setIn } from "../../model/json";
import { schemaFields } from "../../model/schemaText";
import { exampleSentence, formatLoose, parseLoose } from "../../model/values";
import { ValueInput } from "./ValueInput";

export interface ExampleCardProps {
  example: Json;
  exits: string[];
  iface: Interface | null;
  onChange?(next: Json): void;       // absent: read-only
  onDuplicate?(): void;
  onMoveUp?(): void;
  onMoveDown?(): void;
  onRemove?(): void;
}

const TITLE_MAX = 120;

function truncate(text: string): string {
  return text.length > TITLE_MAX ? `${text.slice(0, TITLE_MAX - 1)}…` : text;
}

type Part = "inputs" | "outputs";

export function ExampleCard(props: ExampleCardProps): ReactElement | null {
  const { example, exits, iface, onChange, onDuplicate, onMoveUp, onMoveDown, onRemove } = props;
  const ex: JsonObject = isObject(example) ? example : {};
  const exit = typeof ex.exit === "string" ? ex.exit : "done";
  const readOnly = onChange === undefined;
  const sentence = exampleSentence(example, iface);
  const exitSchema = iface?.exits.find((x) => x.name === exit)?.schema ?? null;
  const exitOptions = [...exits, ...(exits.includes("error") ? [] : ["error"])];
  const known = exitOptions.includes(exit);

  function set(part: Part, field: string, value: Json | undefined): void {
    let next = setIn(ex, [part, field], value);
    const left = (next as JsonObject)[part];
    if (part === "outputs" && isObject(left) && Object.keys(left).length === 0) next = setIn(next, ["outputs"], undefined);
    onChange?.(next);
  }

  const outputs = isObject(ex.outputs) ? ex.outputs : {};
  const outFields = schemaFields(exitSchema);
  const showOutputs = iface === null || outFields.length > 0 || Object.keys(outputs).length > 0;

  return (
    <article className="wg-example-card" aria-label={sentence}>
      <header>
        <span className="wg-example-title" title={sentence}>{truncate(sentence)}</span>
      </header>
      <label className="wg-field">
        <span>Exit</span>
        <select value={exit} disabled={readOnly} onChange={(e) => onChange?.(setIn(ex, ["exit"], e.target.value))}>
          {!known && <option value={exit}>{exit} (unknown)</option>}
          {exitOptions.map((x) => <option key={x} value={x}>{x}</option>)}
        </select>
        {!known && <span className="wg-flag">not an exit of this step</span>}
      </label>
      <Values title="Inputs" part="inputs" values={isObject(ex.inputs) ? ex.inputs : {}} schema={iface?.inputs ?? null}
              known={iface !== null} readOnly={readOnly} onSet={set} />
      {showOutputs && (
        <Values title={`Outputs (${exit})`} part="outputs" values={outputs} schema={exitSchema} known={iface !== null}
                readOnly={readOnly} onSet={set} optional />
      )}
      {!readOnly && (
        <div className="wg-row">
          {onDuplicate !== undefined && <button type="button" onClick={onDuplicate}>Duplicate</button>}
          <button type="button" aria-label="Move example up" disabled={onMoveUp === undefined} onClick={onMoveUp}>↑</button>
          <button type="button" aria-label="Move example down" disabled={onMoveDown === undefined} onClick={onMoveDown}>↓</button>
          {onRemove !== undefined && <button type="button" className="wg-danger" onClick={onRemove}>Remove</button>}
        </div>
      )}
    </article>
  );
}

interface ValuesProps {
  title: string;
  part: Part;
  values: JsonObject;
  schema: JsonSchema | null;
  known: boolean;                    // the interface is known (so fields outside the schema are flagged)
  readOnly: boolean;
  optional?: boolean;                // outputs: a subset of fields is allowed
  onSet(part: Part, field: string, value: Json | undefined): void;
}

function Values({ title, part, values, schema, known, readOnly, optional = false, onSet }: ValuesProps): ReactElement {
  const [newKey, setNewKey] = useState("");
  const fields = schemaFields(schema);
  const names = new Set(fields.map((f) => f.name));
  const extra = Object.keys(values).filter((k) => !names.has(k));
  const free = !known || fields.length === 0;
  return (
    <fieldset className="wg-values" disabled={readOnly}>
      <legend>{title}</legend>
      {fields.map((f) => (
        <ValueInput key={f.name} label={f.name} schema={f.schema} value={values[f.name]} required={!optional && f.required}
                    onChange={(v) => onSet(part, f.name, v)} />
      ))}
      {extra.map((k) => (
        <div key={k} className="wg-row">
          {free ? (
            <label className="wg-field">
              <span>{k}</span>
              <input className="wg-mono" value={formatLoose(values[k] as Json)}
                     onChange={(e) => onSet(part, k, parseLoose(e.target.value))} />
            </label>
          ) : (
            <>
              <span className="wg-mono">{k} = {formatLoose(values[k] as Json)}</span>
              <span className="wg-flag">not in schema</span>
            </>
          )}
          <button type="button" aria-label={`Remove ${k}`} onClick={() => onSet(part, k, undefined)}>×</button>
        </div>
      ))}
      {free && !readOnly && (
        <div className="wg-row">
          <input aria-label={`New ${part === "inputs" ? "input" : "output"} name`} placeholder="field" value={newKey}
                 onChange={(e) => setNewKey(e.target.value)} />
          <button type="button" disabled={newKey === "" || Object.hasOwn(values, newKey)}
                  onClick={() => { onSet(part, newKey, ""); setNewKey(""); }}>Add</button>
        </div>
      )}
    </fieldset>
  );
}

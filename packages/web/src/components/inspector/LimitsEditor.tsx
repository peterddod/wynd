// max_traversals / timeout / retries (+ unknown keys) (`$DRAFTS/07 §7.7`). Numeric text is written as a number, a
// JSON mapping (retries: {run, validation, tool}) as a mapping, anything else as a string (an expression); empty
// deletes the key. max_traversals shows the validator's automatic default on a cycle.
import type { ReactElement } from "react";
import { useDesign, useMeta } from "../../api/context";
import type { Json } from "../../api/types";
import { branchKey, edgeAt, setBranchMapValue } from "../../model/processDoc";
import { formatLoose } from "../../model/values";
import { useDesignState } from "../graph/designHooks";

export interface LimitsEditorProps {
  e: number;
  b: number;
  onCycle: boolean;                  // drives the max_traversals placeholder
  readOnly?: boolean;
}

const NUMBER = /^-?\d+(\.\d+)?$/;

export function limitValue(text: string): Json | undefined {
  const t = text.trim();
  if (t === "") return undefined;
  if (NUMBER.test(t)) return Number(t);
  if (t.startsWith("{") || t.startsWith("[")) {
    try {
      return JSON.parse(t) as Json;
    } catch {
      // not JSON: keep the text
    }
  }
  return text;
}

export function LimitsEditor({ e, b, onCycle, readOnly = false }: LimitsEditorProps): ReactElement | null {
  const design = useDesign();
  const meta = useMeta();
  const s = useDesignState();
  const view = s === null ? null : edgeAt(s.process.doc, e);
  const branch = view?.branches[b];
  if (view === null || view === undefined || branch === undefined) return null;
  const limits = branch.limits;
  const fields = meta?.limit_fields ?? ["max_traversals", "timeout", "retries"];
  const keys = [...fields, ...Object.keys(limits).filter((k) => !fields.includes(k))];
  const auto = meta?.default_max_traversals ?? 10;

  function placeholder(key: string): string | undefined {
    if (key === "max_traversals") return onCycle ? `${auto} (auto — on a cycle)` : "not needed (not on a cycle)";
    if (key === "timeout") return "seconds, or an expression";
    if (key === "retries") return '{"run": 0, "validation": 2, "tool": 1}';
    return undefined;
  }

  return (
    <fieldset className="wg-limits">
      <legend>Limits</legend>
      {keys.map((key) => (
        <label key={key} className="wg-field">
          <span>{key}</span>
          <input className="wg-mono" value={limits[key] === undefined ? "" : formatLoose(limits[key] as Json)}
                 placeholder={placeholder(key)} readOnly={readOnly}
                 onChange={(ev) => design.apply(`edit branch ${branchKey(view, b)}`, (d) => ({
                   ...d, process: setBranchMapValue(d.process, e, b, "limits", key, limitValue(ev.target.value)),
                 }))} />
        </label>
      ))}
    </fieldset>
  );
}

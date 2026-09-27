// manual | schedule (cron, timezone, fixed inputs) | webhook (secret env) (`$DRAFTS/07 §10`).
// Stub from WEB-SCAFFOLD; WEB-OPS implements it.
import type { ReactElement } from "react";
import type { JsonSchema, Trigger } from "../../api/types";

export interface TriggerEditorProps {
  value: Trigger;
  onChange(next: Trigger): void;
  inputsSchema: JsonSchema | null;   // for a schedule's fixed inputs
}

export function TriggerEditor(_props: TriggerEditorProps): ReactElement | null {
  return null;
}

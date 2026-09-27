// max_traversals / timeout / retries (+ unknown keys) (`$DRAFTS/07 §7.7`). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";

export interface LimitsEditorProps {
  e: number;
  b: number;
  onCycle: boolean;                  // drives the max_traversals placeholder
  readOnly?: boolean;
}

export function LimitsEditor(_props: LimitsEditorProps): ReactElement | null {
  return null;
}

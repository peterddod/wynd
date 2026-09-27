// Example cards for a proto-step or the process (`$DRAFTS/07 §7.11`). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";
import type { Interface, Json } from "../../api/types";

export interface ExamplesEditorProps {
  label: string;
  examples: Json[];
  exits: string[];
  iface: Interface | null;
  onChange(next: Json[]): void;
  readOnly?: boolean;
}

export function ExamplesEditor(_props: ExamplesEditorProps): ReactElement | null {
  return null;
}

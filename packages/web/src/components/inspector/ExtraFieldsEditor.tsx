// Unknown keys as a JSON textarea parsed on blur: the lossless escape hatch (`$DRAFTS/07 §7.7`).
// Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";
import type { Json } from "../../api/types";

export interface ExtraFieldsEditorProps {
  label: string;
  value: Record<string, Json>;
  onChange(next: Record<string, Json>): void;
  readOnly?: boolean;
}

export function ExtraFieldsEditor(_props: ExtraFieldsEditorProps): ReactElement | null {
  return null;
}

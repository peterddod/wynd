// Field name/type rows with a type <datalist> (`$DRAFTS/07 §7.9`). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";
import type { Json } from "../../api/types";

export interface FieldTableEditorProps {
  rows: [string, Json][];
  onChange(rows: [string, Json][]): void;
  onRename(from: string, to: string): void;
  typeOptions: string[];
  readOnly?: boolean;
}

export function FieldTableEditor(_props: FieldTableEditorProps): ReactElement | null {
  return null;
}

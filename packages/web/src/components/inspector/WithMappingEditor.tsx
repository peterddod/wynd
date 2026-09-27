// `with:` rows per target input field (`$DRAFTS/07 §7.7`). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";
import type { JsonSchema } from "../../api/types";

export interface FieldRow {
  name: string;
  schema: JsonSchema | null;
  required: boolean;
}

export interface WithMappingEditorProps {
  e: number;
  b: number;
  targetFields: FieldRow[] | null;   // null: unknown interface -> free key rows
  readOnly?: boolean;
}

export function WithMappingEditor(_props: WithMappingEditorProps): ReactElement | null {
  return null;
}

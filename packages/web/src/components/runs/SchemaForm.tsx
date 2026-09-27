// One ValueInput per schema property, required marks, uploads for path fields (`$DRAFTS/07 §11.3`).
// Stub from WEB-SCAFFOLD; WEB-OPS implements it.
import type { ReactElement } from "react";
import type { Json, JsonSchema } from "../../api/types";

export interface SchemaFormProps {
  schema: JsonSchema | null;
  value: Record<string, Json>;
  onChange(v: Record<string, Json>): void;
  allowUpload?: boolean;
}

export function SchemaForm(_props: SchemaFormProps): ReactElement | null {
  return null;
}

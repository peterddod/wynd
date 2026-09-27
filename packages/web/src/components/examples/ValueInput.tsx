// A typed value control chosen from the field's schema (`$DRAFTS/07 §7.11`; path fields have `format === "path"`).
// Reused by WEB-OPS' SchemaForm. Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";
import type { Json, JsonSchema } from "../../api/types";

export interface ValueInputProps {
  label: string;
  schema: JsonSchema | null;
  value: Json | undefined;
  onChange(v: Json | undefined): void;   // undefined: the field is cleared
  allowUpload?: boolean;             // path fields: Upload button -> POST /api/uploads
  required?: boolean;
}

export function ValueInput(_props: ValueInputProps): ReactElement | null {
  return null;
}

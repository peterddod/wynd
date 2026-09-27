// Collapsible JSON tree; strings over 200 characters truncated with [show all]; copy-JSON button (`$DRAFTS/07 §11.5`).
// Stub from WEB-SCAFFOLD; WEB-CORE implements it.
import type { ReactElement } from "react";
import type { Json } from "../../api/types";

export interface JsonViewProps {
  value: Json | undefined;
  label?: string;
  collapsed?: boolean | number;      // true: all collapsed; n: expanded to depth n
}

export function JsonView(_props: JsonViewProps): ReactElement | null {
  return null;
}

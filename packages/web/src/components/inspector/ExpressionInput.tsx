// Expression textarea validated by the controller (300 ms debounce) with scope completion (`$DRAFTS/07 §7.7`).
// Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";
import type { Loc } from "../../api/types";

export interface ExpressionInputProps {
  id: string;
  label: string;
  value: string;
  loc: Loc;
  onChange(v: string): void;         // applies the op immediately (autosave debounces)
  readOnly?: boolean;
  placeholder?: string;
}

export function ExpressionInput(_props: ExpressionInputProps): ReactElement | null {
  return null;
}

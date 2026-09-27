// step, cause, inputs, partial outputs, trace pointer, kept workspace (`$DRAFTS/07 §11.4`; PLAN §3.8).
// Stub from WEB-SCAFFOLD; WEB-OPS implements it.
import type { ReactElement } from "react";
import type { ProcessError } from "../../api/types";

export interface ProcessErrorViewProps {
  error: ProcessError;
}

export function ProcessErrorView(_props: ProcessErrorViewProps): ReactElement | null {
  return null;
}

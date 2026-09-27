// "Remove step X and its edges?" + optional unshared local proto delete (`$DRAFTS/07 §7.6`).
// Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";

export interface RemoveStepDialogProps {
  open: boolean;
  step: string;
  onClose(): void;
}

export function RemoveStepDialog(_props: RemoveStepDialogProps): ReactElement | null {
  return null;
}

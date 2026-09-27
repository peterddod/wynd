// New proto-step / existing step / another process (`$DRAFTS/07 §7.6`). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";

export interface AddStepDialogProps {
  open: boolean;
  onClose(): void;
  onAdded?(step: string): void;      // e.g. retarget a branch to the new step ("New step…")
}

export function AddStepDialog(_props: AddStepDialogProps): ReactElement | null {
  return null;
}

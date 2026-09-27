// id, process root, goal -> POST /api/processes, then open it (`$DRAFTS/07 §6.1`). Stub from WEB-SCAFFOLD; WEB-OPS implements it.
import type { ReactElement } from "react";

export interface NewProcessDialogProps {
  open: boolean;
  onClose(): void;
}

export function NewProcessDialog(_props: NewProcessDialogProps): ReactElement | null {
  return null;
}

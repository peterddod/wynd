// Build, trigger, env binding -> POST /api/releases (`$DRAFTS/07 §10`). Also opened from a build job's
// [Create release…]. Stub from WEB-SCAFFOLD; WEB-OPS implements it.
import type { ReactElement } from "react";

export interface NewReleaseDialogProps {
  open: boolean;
  processId: string;
  commit?: string | null;            // preselected build; default the HEAD build
  onClose(): void;
}

export function NewReleaseDialog(_props: NewReleaseDialogProps): ReactElement | null {
  return null;
}

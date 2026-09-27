// "acting on: <process>" / "acting on: nothing (read-only)"; click focuses the process search (`$DRAFTS/07 §9.4`).
// Stub from WEB-SCAFFOLD; WEB-CHAT implements it.
import type { ReactElement } from "react";

export interface ActingOnChipProps {
  actingOn: string | null;
  onClick?(): void;
}

export function ActingOnChip(_props: ActingOnChipProps): ReactElement | null {
  return null;
}

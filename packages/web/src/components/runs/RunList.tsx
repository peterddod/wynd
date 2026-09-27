// Runs of the process, polled every 3 s while any is queued/running (`$DRAFTS/07 §11.1`). Stub from WEB-SCAFFOLD; WEB-OPS implements it.
import type { ReactElement } from "react";

export interface RunListProps {
  processId: string;
  selected: string | null;
  onSelect(runId: string): void;
  onNew(): void;
}

export function RunList(_props: RunListProps): ReactElement | null {
  return null;
}

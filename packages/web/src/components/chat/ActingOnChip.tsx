// "acting on: <process>" / "acting on: nothing (read-only)"; click focuses the process search (`$DRAFTS/07 §9.4`).
import type { ReactElement } from "react";

export interface ActingOnChipProps {
  actingOn: string | null;
  onClick?(): void;
}

export function ActingOnChip({ actingOn, onClick }: ActingOnChipProps): ReactElement {
  const label = actingOn === null ? "acting on: nothing (read-only)" : `acting on: ${actingOn}`;
  const none = actingOn === null ? "" : undefined;
  if (onClick === undefined) return <span className="wc-chip wc-acting" data-none={none}>{label}</span>;
  return (
    <button type="button" className="wc-chip wc-acting" data-none={none} title="Choose the open process" onClick={onClick}>
      {label}
    </button>
  );
}

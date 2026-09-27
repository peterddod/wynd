// Independent badges from `statusBadges` (`$DRAFTS/07 §6.2`).
import type { ReactElement } from "react";
import type { ProcessStatus } from "../../api/types";
import { statusBadges } from "../../model/status";

export interface StatusBadgesProps {
  status: ProcessStatus | null;
}

export function StatusBadges({ status }: StatusBadgesProps): ReactElement | null {
  if (status === null) return null;
  const badges = statusBadges(status);
  if (badges.length === 0) return null;
  return (
    <span className="wo-badges">
      {badges.map((b, i) => (
        <span key={`${b.kind}-${i}`} className={`wo-badge wo-badge-${b.kind}`} title={b.title}>
          {b.label}
        </span>
      ))}
    </span>
  );
}

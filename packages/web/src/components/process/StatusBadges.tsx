// Independent badges from `statusBadges` (`$DRAFTS/07 §6.2`). Stub from WEB-SCAFFOLD; WEB-OPS implements it.
import type { ReactElement } from "react";
import type { ProcessStatus } from "../../api/types";

export interface StatusBadgesProps {
  status: ProcessStatus | null;
}

export function StatusBadges(_props: StatusBadgesProps): ReactElement | null {
  return null;
}

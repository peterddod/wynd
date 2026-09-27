// One branch: order, name, target, condition/else, with, limits, extra keys; "Check (agentic)" + context chips only
// when the edge kind is agentic (`$DRAFTS/07 §7.7`; PLAN §10 amendment 2). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";
import type { BranchView, EdgeView } from "../../model/processDoc";

export interface BranchCardProps {
  edge: EdgeView;
  branch: BranchView;
  expanded: boolean;
  onCycle: boolean;
  readOnly: boolean;
}

export function BranchCard(_props: BranchCardProps): ReactElement | null {
  return null;
}

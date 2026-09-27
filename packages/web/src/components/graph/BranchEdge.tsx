// One edge per branch with its label; ignored branches dashed and muted (`$DRAFTS/07 §7.5`).
// Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { EdgeProps } from "@xyflow/react";
import type { ReactElement } from "react";
import type { BranchEdgeType } from "../../model/graph";

export function BranchEdge(_props: EdgeProps<BranchEdgeType>): ReactElement | null {
  return null;
}

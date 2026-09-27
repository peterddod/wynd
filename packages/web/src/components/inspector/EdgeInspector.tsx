// `b:<e>:<b>`: the whole edge `e` with branch `b` expanded (`$DRAFTS/07 §7.7`). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";

export interface EdgeInspectorProps {
  edgeIndex: number;
  branchIndex: number;
}

export function EdgeInspector(_props: EdgeInspectorProps): ReactElement | null {
  return null;
}

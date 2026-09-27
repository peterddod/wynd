// Deterministic layered layout (`$DRAFTS/07 §7.4`). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.

export interface LayoutNode {
  id: string;
  w: number;
  h: number;
  order: number;
  rank?: "first" | "last";
}

export interface LayoutEdge {
  source: string;
  target: string;
  order: number;
}

export const GAP_X = 100;
export const GAP_Y = 36;

export function layout(nodes: LayoutNode[], edges: LayoutEdge[]): Record<string, { x: number; y: number }> {
  throw new Error("not implemented");
}

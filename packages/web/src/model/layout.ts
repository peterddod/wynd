// Deterministic layered layout (`$DRAFTS/07 §7.4`): DFS back edges, longest-path layers, four barycenter sweeps,
// left-to-right coordinates with each layer centred on y = 0.

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
const COLUMN_W = 220;

export function layout(nodes: LayoutNode[], edges: LayoutEdge[]): Record<string, { x: number; y: number }> {
  const sorted = [...nodes].sort((a, b) => a.order - b.order);
  const ids = new Set(sorted.map((n) => n.id));
  const es = edges.filter((e) => ids.has(e.source) && ids.has(e.target)).sort((a, b) => a.order - b.order);
  const dag = withoutBackEdges(sorted, es);

  const preds = new Map<string, string[]>(sorted.map((n) => [n.id, []]));
  const succs = new Map<string, string[]>(sorted.map((n) => [n.id, []]));
  for (const e of dag) {
    (preds.get(e.target) as string[]).push(e.source);
    (succs.get(e.source) as string[]).push(e.target);
  }

  const layer = new Map<string, number>();
  for (const n of topoOrder(sorted, dag)) {
    const ps = preds.get(n.id) as string[];
    const l = n.rank === "first" || ps.length === 0 ? 0 : Math.max(...ps.map((p) => (layer.get(p) ?? 0) + 1));
    layer.set(n.id, l);
  }
  const steps = sorted.filter((n) => n.rank !== "last").map((n) => layer.get(n.id) as number);
  const last = (steps.length === 0 ? -1 : Math.max(...steps)) + 1;
  for (const n of sorted) if (n.rank === "last") layer.set(n.id, last);

  const depth = Math.max(0, ...layer.values()) + 1;
  const layers: string[][] = Array.from({ length: depth }, () => []);
  for (const n of sorted) (layers[layer.get(n.id) as number] as string[]).push(n.id);

  const pos = new Map<string, number>();
  const index = (): void => layers.forEach((ids) => ids.forEach((id, i) => pos.set(id, i)));
  index();
  const sweep = (range: number[], neighbours: Map<string, string[]>): void => {
    for (const l of range) {
      const ids = layers[l] as string[];
      const key = new Map(ids.map((id) => {
        const ns = neighbours.get(id) as string[];
        const own = pos.get(id) as number;
        return [id, ns.length === 0 ? own : ns.reduce((sum, n) => sum + (pos.get(n) as number), 0) / ns.length];
      }));
      ids.sort((a, b) => (key.get(a) as number) - (key.get(b) as number));
      index();
    }
  };
  const down = Array.from({ length: depth }, (_, i) => i).slice(1);
  const up = Array.from({ length: depth }, (_, i) => depth - 1 - i).slice(1);
  for (let i = 0; i < 2; i++) {
    sweep(down, preds);
    sweep(up, succs);
  }

  const size = new Map(sorted.map((n) => [n.id, n]));
  const out: Record<string, { x: number; y: number }> = {};
  layers.forEach((ids, l) => {
    const heights = ids.map((id) => (size.get(id) as LayoutNode).h);
    const total = heights.reduce((s, h) => s + h, 0) + GAP_Y * Math.max(0, ids.length - 1);
    let y = 0;
    ids.forEach((id, i) => {
      out[id] = { x: l * (COLUMN_W + GAP_X), y: y - total / 2 };
      y += (heights[i] as number) + GAP_Y;
    });
  });
  return out;
}

/** DFS from the rank-first nodes, then from each unvisited node in order; an edge to a node on the DFS stack is a back
 *  edge; self-loops are ignored. Returns the remaining edges (the DAG). */
function withoutBackEdges(sorted: LayoutNode[], es: LayoutEdge[]): LayoutEdge[] {
  const out = new Map<string, LayoutEdge[]>();
  for (const e of es) out.set(e.source, [...(out.get(e.source) ?? []), e]);
  const state = new Map<string, "stack" | "done">();
  const back = new Set<LayoutEdge>();
  const visit = (v: string): void => {
    state.set(v, "stack");
    for (const e of out.get(v) ?? []) {
      if (e.source === e.target) continue;
      const s = state.get(e.target);
      if (s === "stack") back.add(e);
      else if (s === undefined) visit(e.target);
    }
    state.set(v, "done");
  };
  for (const n of [...sorted.filter((n) => n.rank === "first"), ...sorted]) if (!state.has(n.id)) visit(n.id);
  return es.filter((e) => e.source !== e.target && !back.has(e));
}

/** Kahn's algorithm; among ready nodes the lowest `order` goes first. */
function topoOrder(sorted: LayoutNode[], dag: LayoutEdge[]): LayoutNode[] {
  const indeg = new Map(sorted.map((n) => [n.id, 0]));
  for (const e of dag) indeg.set(e.target, (indeg.get(e.target) as number) + 1);
  const ready = sorted.filter((n) => indeg.get(n.id) === 0);
  const byId = new Map(sorted.map((n) => [n.id, n]));
  const out: LayoutNode[] = [];
  while (ready.length > 0) {
    ready.sort((a, b) => a.order - b.order);
    const n = ready.shift() as LayoutNode;
    out.push(n);
    for (const e of dag) {
      if (e.source !== n.id) continue;
      const d = (indeg.get(e.target) as number) - 1;
      indeg.set(e.target, d);
      if (d === 0) ready.push(byId.get(e.target) as LayoutNode);
    }
  }
  return out;
}


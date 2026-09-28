// Branches on cycles, Tarjan SCC over step targets (`$DRAFTS/07 §7.4`). A branch u -> v lies on a cycle iff u and v
// share a strongly connected component (a self-loop included), the rule the validator uses to fill max_traversals
// (PLAN §15 item 8). Ignored branches (after the else) are dropped, as in the normalised definition.
import type { Json } from "../api/types";
import { edgesOf } from "./processDoc";

/** "e:b" keys of branches that lie on a cycle. */
export function branchesOnCycles(doc: Json): Set<string> {
  const arcs: { from: string; to: string; key: string }[] = [];
  for (const e of edgesOf(doc)) {
    for (const b of e.branches) {
      if (b.ignored || b.target.type !== "step") continue;
      arcs.push({ from: e.fromStep, to: b.target.step, key: `${e.index}:${b.index}` });
    }
  }
  const comp = scc(arcs);
  return new Set(arcs.filter((a) => comp.get(a.from) === comp.get(a.to)).map((a) => a.key));
}

/** Tarjan: node -> component id. */
function scc(arcs: { from: string; to: string }[]): Map<string, number> {
  const adj = new Map<string, string[]>();
  for (const a of arcs) {
    adj.set(a.from, [...(adj.get(a.from) ?? []), a.to]);
    if (!adj.has(a.to)) adj.set(a.to, []);
  }
  const index = new Map<string, number>();
  const low = new Map<string, number>();
  const onStack = new Set<string>();
  const stack: string[] = [];
  const comp = new Map<string, number>();
  let counter = 0;
  let comps = 0;

  function visit(v: string): void {
    index.set(v, counter);
    low.set(v, counter);
    counter += 1;
    stack.push(v);
    onStack.add(v);
    for (const w of adj.get(v) ?? []) {
      if (!index.has(w)) {
        visit(w);
        low.set(v, Math.min(low.get(v) as number, low.get(w) as number));
      } else if (onStack.has(w)) {
        low.set(v, Math.min(low.get(v) as number, index.get(w) as number));
      }
    }
    if (low.get(v) !== index.get(v)) return;
    for (;;) {
      const w = stack.pop() as string;
      onStack.delete(w);
      comp.set(w, comps);
      if (w === v) break;
    }
    comps += 1;
  }

  for (const v of adj.keys()) if (!index.has(v)) visit(v);
  return comp;
}

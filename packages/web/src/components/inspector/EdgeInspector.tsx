// `b:<e>:<b>`: the whole edge `e` with branch `b` expanded (`$DRAFTS/07 §7.7`). Edge kinds come from
// `meta.edge_kinds` (deterministic, agentic).
import type { ReactElement } from "react";
import { useDesign, useMeta } from "../../api/context";
import { branchesOnCycles } from "../../model/cycles";
import { addBranch, edgeAt, parseTarget, removeEdge, setEdgeKind } from "../../model/processDoc";
import { useUrlState } from "../../state/url";
import { isReadOnly, useDesignState } from "../graph/designHooks";
import { BranchCard, targetOptions } from "./BranchCard";

export interface EdgeInspectorProps {
  edgeIndex: number;
  branchIndex: number;
}

export function EdgeInspector({ edgeIndex, branchIndex }: EdgeInspectorProps): ReactElement | null {
  const design = useDesign();
  const meta = useMeta();
  const s = useDesignState();
  const [, setUrl] = useUrlState();
  if (s === null) return null;
  const view = edgeAt(s.process.doc, edgeIndex);
  if (view === null) return <p className="wg-muted">This edge no longer exists.</p>;
  const readOnly = isReadOnly(s);
  const cycles = branchesOnCycles(s.process.doc);
  const kinds = meta?.edge_kinds ?? ["deterministic", "agentic"];

  function add(value: string): void {
    if (value === "") return;
    let index = 0;
    design.apply(`add branch ${view?.from ?? ""}`, (d) => {
      const r = addBranch(d.process, edgeIndex, parseTarget(value));
      index = r.branchIndex;
      return { ...d, process: r.doc };
    }, { structural: true });
    setUrl({ sel: `b:${edgeIndex}:${index}` });
  }

  return (
    <div className="wg-edge-inspector">
      <header className="wg-inspector-head">
        <h2>{view.fromStep} · {view.fromExit}</h2>
        <label className="wg-field">
          <span>Kind</span>
          <select value={view.kind} disabled={readOnly}
                  onChange={(e) => design.apply(`set edge kind ${view.from}`, (d) => ({ ...d, process: setEdgeKind(d.process, edgeIndex, e.target.value) }))}>
            {[...new Set([...kinds, view.kind])].map((k) => <option key={k} value={k}>{k}</option>)}
          </select>
        </label>
        {!readOnly && (
          <button type="button" className="wg-danger"
                  onClick={() => {
                    design.apply(`remove edge ${view.from}`, (d) => ({ ...d, process: removeEdge(d.process, edgeIndex) }), { structural: true });
                    setUrl({ sel: null });
                  }}>Remove edge</button>
        )}
      </header>
      <ol className="wg-branches" aria-label="Branches">
        {view.branches.map((b) => (
          <BranchCard key={b.index} edge={view} branch={b} expanded={b.index === branchIndex} readOnly={readOnly}
                      onCycle={cycles.has(`${edgeIndex}:${b.index}`)} />
        ))}
      </ol>
      {!readOnly && (
        <label className="wg-field">
          <span>+ Add branch</span>
          <select value="" onChange={(e) => add(e.target.value)}>
            <option value="">Route to…</option>
            {targetOptions(s.process.doc).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
      )}
    </div>
  );
}

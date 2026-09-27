// Controlled React Flow editor over the design session (`$DRAFTS/07 §7.6`). Nodes and edges are derived from the
// design state with `toFlow()`; positions come from `layout()` (recomputed only when the structure changes) plus
// per-browser drag overrides. Every gesture is a thin wrapper over a tested `processDoc` op applied through the
// session; selection is URL `sel`.
import {
  ReactFlow, type Connection, type Edge, type EdgeChange, type Node, type NodeChange, type OnBeforeDelete,
} from "@xyflow/react";
import { useMemo, useState, type KeyboardEvent, type ReactElement } from "react";
import { useDesign, useMeta } from "../../api/context";
import { type BranchEdgeData, layoutInput, structureKey, toFlow } from "../../model/graph";
import { layout } from "../../model/layout";
import {
  branchKey, edgeAt, EXIT_PREFIX, IGNORE, parseTarget, removeBranch, routeExit, targetString, updateBranch, type Target,
} from "../../model/processDoc";
import type { DesignSession } from "../../state/design";
import { useTheme } from "../../state/theme";
import { useUrlState } from "../../state/url";
import { AddStepDialog } from "./AddStepDialog";
import { BranchEdge } from "./BranchEdge";
import { isReadOnly, protoDocs, useDesignState } from "./designHooks";
import { GraphToolbar } from "./GraphToolbar";
import { InputsNode } from "./InputsNode";
import { clearOverrides, loadOverrides, saveOverride, type XY } from "./positions";
import { RemoveStepDialog } from "./RemoveStepDialog";
import { RenameStepDialog } from "./RenameStepDialog";
import { StepNode } from "./StepNode";
import { TerminalNode } from "./TerminalNode";

const nodeTypes = { step: StepNode, terminal: TerminalNode, inputs: InputsNode };
const edgeTypes = { branch: BranchEdge };

/** The branch target a node id stands for ("s:<step>", "x:<exit>", "x:$ignore"); null for anything else. */
export function targetOfNode(id: string | null | undefined): Target | null {
  if (id?.startsWith("s:") === true) return { type: "step", step: id.slice(2) };
  if (id?.startsWith("x:") === true) return parseTarget(id === `x:${IGNORE}` ? IGNORE : `${EXIT_PREFIX}${id.slice(2)}`);
  return null;
}

export function isValidConnection(c: Connection | Edge): boolean {
  return c.source.startsWith("s:") && (c.sourceHandle ?? "").startsWith("o:") && targetOfNode(c.target) !== null;
}

/** Test hook for drag-connect (pointer drags are unreliable in jsdom): applies `routeExit` for a connection from an
 *  exit handle and returns the new selection ("b:<e>:<b>"), or null when the connection is not valid. */
export function handleConnect(design: DesignSession, connection: Connection): string | null {
  const target = targetOfNode(connection.target);
  if (!isValidConnection(connection) || target === null) return null;
  const step = connection.source.slice(2);
  const exit = (connection.sourceHandle as string).slice(2);
  let routed: { edgeIndex: number; branchIndex: number } | null = null;
  design.apply(`route ${step}.${exit} → ${targetString(target)}`, (d) => {
    const r = routeExit(d.process, step, exit, target);
    routed = { edgeIndex: r.edgeIndex, branchIndex: r.branchIndex };
    return { ...d, process: r.doc };
  }, { structural: true });
  const done = routed as { edgeIndex: number; branchIndex: number } | null;
  return done === null ? null : `b:${done.edgeIndex}:${done.branchIndex}`;
}

type DialogState = { kind: "add" } | { kind: "rename"; step: string } | { kind: "remove"; step: string } | null;

export function GraphEditor(): ReactElement | null {
  const design = useDesign();
  const s = useDesignState();
  const meta = useMeta();
  const [url, setUrl] = useUrlState();
  const [theme] = useTheme();
  const [dialog, setDialog] = useState<DialogState>(null);
  const [dragging, setDragging] = useState<Record<string, XY>>({});
  const [measured, setMeasured] = useState<Record<string, { width: number; height: number }>>({});
  const [overrideTick, setOverrideTick] = useState(0);
  const root = meta?.workspace.root ?? "";
  const pid = s?.processId ?? "";
  const readOnly = s === null || isReadOnly(s);

  const flow = useMemo(() => (s === null || s.parseError !== null ? { nodes: [], edges: [] } : toFlow({
    process: s.process.doc,
    protos: protoDocs(s),
    steps: s.steps,
    issues: s.issuesStale ? null : (s.report?.issues ?? []),
    readOnly,
  })), [s, readOnly]);
  const key = structureKey(flow);
  // Keyed on the structure only: text edits never move nodes.
  const auto = useMemo(() => {
    const input = layoutInput(flow);
    return layout(input.nodes, input.edges);
  }, [key]);
  const overrides = useMemo(() => loadOverrides(root, pid), [root, pid, overrideTick]);

  if (s === null) return null;
  if (s.parseError !== null) {
    const at = s.parseError.line === null ? "" : ` (line ${s.parseError.line}, col ${s.parseError.column ?? "?"})`;
    return (
      <div className="wg-graph-editor wg-parse-error" role="alert">
        process.yaml can't be parsed: {s.parseError.message}{at}. Fix it in your editor or ask the chat.
      </div>
    );
  }

  const nodes: Node[] = flow.nodes.map((n) => ({
    ...n,
    position: dragging[n.id] ?? overrides[n.id] ?? auto[n.id] ?? { x: 0, y: 0 },
    selected: n.id === url.sel,
    ...(measured[n.id] === undefined ? {} : { measured: measured[n.id] }),
  }));
  const edges: Edge[] = flow.edges.map((e) => ({
    ...e,
    selected: e.id === url.sel,
    ...(e.type === "branch" ? { reconnectable: readOnly ? false : ("target" as const) } : {}),
  }));

  function select(id: string | null): void {
    if (id !== url.sel) setUrl({ sel: id });
  }

  function onNodesChange(changes: NodeChange[]): void {
    for (const c of changes) {
      if (c.type === "position" && c.position !== undefined) {
        const pos = c.position;
        setDragging((d) => ({ ...d, [c.id]: pos }));
      } else if (c.type === "dimensions" && c.dimensions !== undefined) {
        const dims = c.dimensions;
        setMeasured((m) => ({ ...m, [c.id]: dims }));
      } else if (c.type === "select" && c.selected) {
        select(c.id);
      }
    }
  }

  function onEdgesChange(changes: EdgeChange[]): void {
    for (const c of changes) if (c.type === "select" && c.selected && c.id.startsWith("b:")) select(c.id);
  }

  function onNodeDragStop(_: unknown, node: Node): void {
    saveOverride(root, pid, node.id, node.position);
    setDragging((d) => Object.fromEntries(Object.entries(d).filter(([id]) => id !== node.id)));
    setOverrideTick((t) => t + 1);
  }

  function onReconnect(old: Edge, next: Connection): void {
    const d = old.data as BranchEdgeData | undefined;
    const target = targetOfNode(next.target);
    if (d === undefined || target === null || next.source !== old.source || next.sourceHandle !== old.sourceHandle) return;
    const view = edgeAt(design.processDoc(), d.edgeIndex);
    if (view === null) return;
    design.apply(`retarget ${branchKey(view, d.branchIndex)}`,
      (drafts) => ({ ...drafts, process: updateBranch(drafts.process, d.edgeIndex, d.branchIndex, { target }) }),
      { structural: true });
  }

  const onBeforeDelete: OnBeforeDelete = async ({ nodes: gone, edges: cut }) => {
    if (readOnly) return false;
    const step = gone.find((n) => n.id.startsWith("s:"));
    if (step !== undefined) {
      setDialog({ kind: "remove", step: step.id.slice(2) });
      return false;
    }
    const branches = cut.flatMap((e) => (e.type === "branch" && e.data !== undefined ? [e.data as BranchEdgeData] : []))
      .sort((a, b) => b.edgeIndex - a.edgeIndex || b.branchIndex - a.branchIndex);
    if (branches.length === 0) return false;
    const doc = design.processDoc();
    const label = branches.map((b) => {
      const view = edgeAt(doc, b.edgeIndex);
      return view === null ? `edge ${b.edgeIndex}` : branchKey(view, b.branchIndex);
    }).join(", ");
    design.apply(`remove branch ${label}`, (d) => ({
      ...d, process: branches.reduce((out, b) => removeBranch(out, b.edgeIndex, b.branchIndex), d.process),
    }), { structural: true });
    select(null);
    return false;
  };

  function onKeyDown(e: KeyboardEvent<HTMLDivElement>): void {
    if (e.key === "Escape") select(null);
  }

  return (
    <div className="wg-graph-editor" onKeyDown={onKeyDown}>
      <GraphToolbar readOnly={readOnly} onAddStep={() => setDialog({ kind: "add" })}
                    onRelayout={() => { clearOverrides(root, pid); setDragging({}); setOverrideTick((t) => t + 1); }} />
      <div className="wg-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          colorMode={theme}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={(c) => { const sel = handleConnect(design, c); if (sel !== null) select(sel); }}
          isValidConnection={isValidConnection}
          onReconnect={onReconnect}
          onBeforeDelete={onBeforeDelete}
          onNodeClick={(_, n) => select(n.id)}
          onEdgeClick={(_, e) => { if (e.id.startsWith("b:")) select(e.id); }}
          onPaneClick={() => select(null)}
          onNodeDoubleClick={(_, n) => { if (!readOnly && n.id.startsWith("s:")) setDialog({ kind: "rename", step: n.id.slice(2) }); }}
          onNodeDragStop={onNodeDragStop}
          nodesConnectable={!readOnly}
          edgesReconnectable={!readOnly}
          deleteKeyCode={readOnly ? null : ["Delete", "Backspace"]}
          nodesFocusable
          edgesFocusable
          fitView
        />
      </div>
      <AddStepDialog open={dialog?.kind === "add"} onClose={() => setDialog(null)} />
      {dialog?.kind === "rename" && <RenameStepDialog open step={dialog.step} onClose={() => setDialog(null)} />}
      {dialog?.kind === "remove" && <RemoveStepDialog open step={dialog.step} onClose={() => setDialog(null)} />}
    </div>
  );
}

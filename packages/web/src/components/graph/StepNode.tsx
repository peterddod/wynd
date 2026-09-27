// Step node: title, kind/phase badges, one source handle per exit (`$DRAFTS/07 §7.5`). The implicit `error` exit is
// muted at the bottom; exits used by an edge but not declared are shown in red.
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { ReactElement } from "react";
import type { StepNodeType } from "../../model/graph";
import { useOpenProcess } from "../../state/openProcess";
import { classes } from "./designHooks";

export function StepNode({ data, selected }: NodeProps<StepNodeType>): ReactElement | null {
  const openProcess = useOpenProcess();
  const child = data.use.startsWith("process:") ? data.use.slice("process:".length) : null;
  const connectable = !data.readOnly;
  return (
    <div className={classes("wg-node", "wg-step", `wg-phase-${data.phase}`, selected && "wg-selected",
                            data.issueCount > 0 && "wg-has-issues")}>
      <Handle type="target" position={Position.Left} id="t" isConnectable={connectable}
              className={classes(!connectable && "wg-handle-hidden")} />
      <div className="wg-node-head">
        <span className="wg-node-title">{data.name}</span>
        {data.isEntry && <span className="wg-chip">entry</span>}
        {data.isErrorHandler && <span className="wg-chip">on error</span>}
        {data.isFinally && <span className="wg-chip">finally</span>}
      </div>
      <div className="wg-node-badges">
        <span className={classes("wg-badge", `wg-kind-${data.stepKind ?? "none"}`)}>{data.stepKind ?? "not compiled"}</span>
        <span className={classes("wg-badge", `wg-phase-badge-${data.phase}`)}>{data.phase}</span>
        {data.tier !== null && <span className="wg-badge">{data.tier}</span>}
        {data.issueCount > 0 && (
          <span className="wg-badge wg-badge-error">{data.issueCount} {data.issueCount === 1 ? "issue" : "issues"}</span>
        )}
      </div>
      <ul className="wg-exits" aria-label="Exits">
        {data.exits.map((x) => (
          <li key={x.name} className={classes("wg-exit", x.implicit && "wg-exit-implicit", !x.declared && "wg-exit-undeclared",
                                             !x.routed && "wg-exit-unrouted")}>
            <span>{x.name}{x.declared ? "" : " (not declared)"}</span>
            <Handle type="source" position={Position.Right} id={`o:${x.name}`} isConnectable={connectable}
                    className={classes(!connectable && "wg-handle-hidden")} />
          </li>
        ))}
      </ul>
      {child !== null && (
        <button type="button" className="wg-open-process nodrag" onClick={() => void openProcess(child)}>
          Open process
        </button>
      )}
    </div>
  );
}

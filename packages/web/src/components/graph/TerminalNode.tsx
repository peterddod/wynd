// `x:<exit>` terminal node; `x:$ignore` rendered muted (`$DRAFTS/07 §7.5`; PLAN §10 amendment 2). A `$exit.<name>`
// target that the process does not declare is shown as an error.
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { ReactElement } from "react";
import type { TerminalNodeType } from "../../model/graph";
import { IGNORE } from "../../model/processDoc";
import { classes } from "./designHooks";

export function TerminalNode({ data, selected, isConnectable }: NodeProps<TerminalNodeType>): ReactElement | null {
  const ignore = data.exit === IGNORE;
  return (
    <div className={classes("wg-node", "wg-terminal", ignore && "wg-terminal-ignore", !data.declared && "wg-terminal-undeclared",
                            selected && "wg-selected")}>
      <Handle type="target" position={Position.Left} id="t" isConnectable={isConnectable}
              className={classes(!isConnectable && "wg-handle-hidden")} />
      <span className="wg-node-title">{ignore ? "ignored" : `$exit.${data.exit}`}</span>
      {!data.declared && <span className="wg-badge wg-badge-error">not declared</span>}
    </div>
  );
}

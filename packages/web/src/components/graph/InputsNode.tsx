// `in` decoration node listing the process inputs (`$DRAFTS/07 §7.5`); its one edge goes to the entry step.
import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { ReactElement } from "react";
import type { InputsNodeType } from "../../model/graph";
import { classes } from "./designHooks";

export function InputsNode({ data, selected }: NodeProps<InputsNodeType>): ReactElement | null {
  return (
    <div className={classes("wg-node", "wg-inputs", selected && "wg-selected")}>
      <span className="wg-node-title">Inputs</span>
      <span className="wg-inputs-fields">{data.fields.length === 0 ? "none" : data.fields.join(", ")}</span>
      <Handle type="source" position={Position.Right} id="o" isConnectable={false} />
    </div>
  );
}

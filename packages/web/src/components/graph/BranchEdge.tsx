// One edge per branch with its label; ignored branches dashed and muted, branches with issues in the error colour,
// cycle branches with ↻ and their max_traversals (or the validator's default) (`$DRAFTS/07 §7.5`). An edge that runs
// right to left (a back edge of the layout) is routed around the nodes as a smooth step.
import { BaseEdge, EdgeLabelRenderer, getBezierPath, getSmoothStepPath, type EdgeProps } from "@xyflow/react";
import type { ReactElement } from "react";
import { useMeta } from "../../api/context";
import type { BranchEdgeType } from "../../model/graph";
import { formatLoose } from "../../model/values";
import { useUrlState } from "../../state/url";
import { classes } from "./designHooks";

export function BranchEdge(props: EdgeProps<BranchEdgeType>): ReactElement | null {
  const meta = useMeta();
  const [, setUrl] = useUrlState();
  const { id, data, selected, markerEnd, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition } = props;
  const geometry = { sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition };
  const [path, labelX, labelY] = targetX < sourceX
    ? getSmoothStepPath({ ...geometry, offset: 24 })
    : getBezierPath(geometry);
  const auto = `${meta?.default_max_traversals ?? 10} (auto)`;
  const cycle = data?.onCycle === true
    ? ` ↻ ${data.maxTraversals === null ? auto : formatLoose(data.maxTraversals)}`
    : "";
  const text = `${data?.label ?? ""}${cycle}`.trim();
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd}
                className={classes("wg-edge", data?.ignored === true && "wg-edge-ignored", data?.hasIssues === true && "wg-edge-error",
                                   selected === true && "wg-edge-selected")} />
      {text !== "" && (
        <EdgeLabelRenderer>
          <div
            className={classes("wg-edge-label", "nodrag", "nopan", data?.ignored === true && "wg-edge-ignored",
                               data?.hasIssues === true && "wg-edge-error", selected === true && "wg-edge-selected")}
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
            title={data?.ignored === true ? "Ignored: comes after the else branch" : undefined}
            onClick={() => setUrl({ sel: id })}
          >
            {text}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

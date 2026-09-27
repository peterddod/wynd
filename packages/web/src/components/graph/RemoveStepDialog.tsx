// "Remove step X and its edges?" + optional delete of an unshared local proto file (`$DRAFTS/07 §7.6`). Compiled
// `steps/<name>/` source is never deleted by the UI.
import { useState, type ReactElement } from "react";
import { useDesign } from "../../api/context";
import { removeStep } from "../../model/processDoc";
import { useUrlState } from "../../state/url";
import { Dialog } from "../common/Dialog";
import { protoPathOf, stepsByProto, useDesignState } from "./designHooks";

export interface RemoveStepDialogProps {
  open: boolean;
  step: string;
  onClose(): void;
}

export function RemoveStepDialog({ open, step, onClose }: RemoveStepDialogProps): ReactElement | null {
  const design = useDesign();
  const s = useDesignState();
  const [, setUrl] = useUrlState();
  const [deleteProto, setDeleteProto] = useState(true);
  if (s === null) return null;
  const info = s.steps[step];
  const path = protoPathOf(s, step);
  const otherKeys = path === null ? [] : (stepsByProto(s)[path] ?? []).filter((k) => k !== step);
  const otherProcesses = (info?.used_by ?? []).filter((p) => p !== s.processId);
  const offer = path !== null && (info?.ref_kind ?? "local") === "local" && otherKeys.length === 0 && otherProcesses.length === 0;

  function remove(): void {
    const drop = offer && deleteProto ? path : null;
    design.apply(`remove step ${step}`, (d) => ({
      process: removeStep(d.process, step),
      protos: drop === null ? d.protos : { ...d.protos, [drop]: null },
    }), { structural: true });
    setUrl({ sel: null });
    onClose();
  }

  return (
    <Dialog open={open} title={`Remove step ${step}`} onClose={onClose} className="wg-dialog"
            actions={<>
              <button type="button" onClick={onClose}>Cancel</button>
              <button type="button" onClick={remove}>Remove</button>
            </>}>
      <p>Remove step {step} and its edges?</p>
      {offer && (
        <label>
          <input type="checkbox" checked={deleteProto} onChange={(e) => setDeleteProto(e.target.checked)} />
          Also delete proto-step file <span className="wg-mono">{path}</span>
        </label>
      )}
    </Dialog>
  );
}

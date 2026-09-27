// Rename with reference rewrite report (`$DRAFTS/07 §7.6`): the key keeps its position, every reference is rewritten
// and expressions that cannot be lexed are listed for a manual fix.
import { useState, type ReactElement } from "react";
import { useDesign, useMeta } from "../../api/context";
import type { Loc } from "../../api/types";
import { formatLoc } from "../../model/issues";
import { renameStep, stepNames } from "../../model/processDoc";
import { useUrlState } from "../../state/url";
import { Dialog } from "../common/Dialog";
import { stepNameProblem, useDesignState } from "./designHooks";
import { renameOverride } from "./positions";

export interface RenameStepDialogProps {
  open: boolean;
  step: string;
  onClose(): void;
}

export function RenameStepDialog({ open, step, onClose }: RenameStepDialogProps): ReactElement | null {
  const design = useDesign();
  const meta = useMeta();
  const s = useDesignState();
  const [url, setUrl] = useUrlState();
  const [name, setName] = useState<string | null>(null);
  const [report, setReport] = useState<{ rewritten: number; unparsed: Loc[] } | null>(null);
  if (s === null) return null;
  const value = name ?? step;
  const problem = value === step ? null : stepNameProblem(value, stepNames(s.process.doc));

  function close(): void {
    setName(null);
    setReport(null);
    onClose();
  }

  function rename(): void {
    if (problem !== null || value === step) return;
    let result: { rewritten: number; unparsed: Loc[] } = { rewritten: 0, unparsed: [] };
    design.apply(`rename step ${step} → ${value}`, (d) => {
      const r = renameStep(d.process, step, value);
      result = { rewritten: r.rewritten, unparsed: r.unparsed };
      return { ...d, process: r.doc };
    }, { structural: true });
    const pid = design.store.get()?.processId ?? "";
    renameOverride(meta?.workspace.root ?? "", pid, step, value);
    if (url.sel === `s:${step}`) setUrl({ sel: `s:${value}` });
    setReport(result);
  }

  return (
    <Dialog open={open} title={`Rename step ${step}`} onClose={close} className="wg-dialog"
            actions={report === null
              ? <><button type="button" onClick={close}>Cancel</button>
                  <button type="button" onClick={rename} disabled={problem !== null || value === step}>Rename</button></>
              : <button type="button" onClick={close}>Close</button>}>
      {report === null ? (
        <>
          <label className="wg-field">
            <span>New name</span>
            <input value={value} onChange={(e) => setName(e.target.value)} aria-invalid={problem !== null}
                   aria-describedby="wg-rename-problem" autoFocus />
          </label>
          {problem !== null && <p id="wg-rename-problem" className="wg-error-text">{problem}</p>}
        </>
      ) : (
        <div role="status">
          <p>Updated {report.rewritten} {report.rewritten === 1 ? "reference" : "references"}.</p>
          {report.unparsed.length > 0 && (
            <>
              <p>{report.unparsed.length} {report.unparsed.length === 1 ? "expression" : "expressions"} could not be updated automatically:</p>
              <ul>{report.unparsed.map((loc) => <li key={formatLoc(loc)} className="wg-mono">{formatLoc(loc)}</li>)}</ul>
            </>
          )}
        </div>
      )}
    </Dialog>
  );
}

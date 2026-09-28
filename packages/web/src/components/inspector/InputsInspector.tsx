// `in`: process inputs and the entry step (`$DRAFTS/07 §7.7`). The entry step's Input is bound from these by field
// name; a mismatch is a validator error, shown inline.
import type { ReactElement } from "react";
import { useDesign, useMeta } from "../../api/context";
import { getIn, isObject } from "../../model/json";
import { issuesAt } from "../../model/issues";
import { setEntry, setFieldMap, stepNames } from "../../model/processDoc";
import { renameField } from "../../model/protoDoc";
import { isReadOnly, useDesignState } from "../graph/designHooks";
import { FieldTableEditor } from "./FieldTableEditor";

export function InputsInspector(): ReactElement | null {
  const design = useDesign();
  const meta = useMeta();
  const s = useDesignState();
  if (s === null) return null;
  const doc = s.process.doc;
  const readOnly = isReadOnly(s);
  const inputs = getIn(doc, ["inputs"]);
  const entry = getIn(doc, ["entry"]);
  const steps = stepNames(doc);
  const issues = s.report === null ? [] : [
    ...issuesAt(s.report.issues, s.processPath, ["entry"]),
    ...issuesAt(s.report.issues, s.processPath, ["inputs"]),
  ];
  return (
    <div className="wg-inputs-inspector">
      <h2>Process inputs</h2>
      <FieldTableEditor rows={isObject(inputs) ? Object.entries(inputs) : []} typeOptions={meta?.proto_types ?? []} readOnly={readOnly}
                        onChange={(rows) => design.apply("edit inputs", (d) => ({ ...d, process: setFieldMap(d.process, ["inputs"], rows) }))}
                        onRename={(from, to) => design.apply("edit inputs", (d) => ({ ...d, process: renameField(d.process, ["inputs"], from, to) }))} />
      <label className="wg-field">
        <span>Entry step</span>
        <select value={typeof entry === "string" ? entry : ""} disabled={readOnly}
                onChange={(e) => design.apply("set entry", (d) => ({ ...d, process: setEntry(d.process, e.target.value) }))}>
          {typeof entry !== "string" && <option value="">(none)</option>}
          {steps.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
      </label>
      <p className="wg-hint">The entry step's inputs are bound from the process inputs by field name.</p>
      {issues.length > 0 && (
        <ul className="wg-issues">
          {issues.map((i, n) => <li key={n} className={`wg-issue wg-issue-${i.severity}`}>{i.code}: {i.message}</li>)}
        </ul>
      )}
    </div>
  );
}

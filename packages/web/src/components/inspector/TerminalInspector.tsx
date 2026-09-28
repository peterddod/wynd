// `x:<exit>`: the process exit's output fields and rename (`$DRAFTS/07 §7.7`). An exit used by an edge but not declared
// can be declared here; `$ignore` explains what ignoring an exit does.
import { useState, type ReactElement } from "react";
import { useDesign, useMeta } from "../../api/context";
import { IGNORE, processExits, renameProcessExit, setFieldMap } from "../../model/processDoc";
import { addExit, outputFields, renameField } from "../../model/protoDoc";
import { useUrlState } from "../../state/url";
import { isReadOnly, useDesignState } from "../graph/designHooks";
import { exitNameProblem } from "./ExitsEditor";
import { FieldTableEditor } from "./FieldTableEditor";

export interface TerminalInspectorProps {
  exit: string;
}

export function TerminalInspector({ exit }: TerminalInspectorProps): ReactElement | null {
  const design = useDesign();
  const meta = useMeta();
  const s = useDesignState();
  const [, setUrl] = useUrlState();
  const [name, setName] = useState<string | null>(null);
  if (s === null) return null;
  const doc = s.process.doc;
  const readOnly = isReadOnly(s);
  if (exit === IGNORE) {
    return (
      <div className="wg-terminal-inspector">
        <h2>Ignored</h2>
        <p>A branch to <span className="wg-mono">$ignore</span> explicitly ignores that exit: taking it routes the run to the
          process error handler (cause <span className="wg-mono">ignored_exit</span>).</p>
      </div>
    );
  }
  const exits = processExits(doc);
  if (!exits.includes(exit)) {
    return (
      <div className="wg-terminal-inspector">
        <h2>$exit.{exit}</h2>
        <p className="wg-error-text">This exit is used by an edge but the process does not declare it.</p>
        {!readOnly && (
          <button type="button" onClick={() => design.apply(`declare exit ${exit}`, (d) => ({ ...d, process: addExit(d.process, exit) }), { structural: true })}>
            Declare exit {exit}
          </button>
        )}
      </div>
    );
  }
  const value = name ?? exit;
  const problem = value === exit ? null : exitNameProblem(value, exits);

  function rename(): void {
    if (problem !== null || value === exit) return;
    design.apply(`rename exit ${exit} → ${value}`, (d) => ({ ...d, process: renameProcessExit(d.process, exit, value) }), { structural: true });
    setName(null);
    setUrl({ sel: `x:${value}` });
  }

  return (
    <div className="wg-terminal-inspector">
      <h2>$exit.{exit}</h2>
      <div className="wg-row">
        <label className="wg-field">
          <span>Exit name</span>
          <input value={value} readOnly={readOnly} onChange={(e) => setName(e.target.value)} aria-invalid={problem !== null} />
        </label>
        {!readOnly && <button type="button" disabled={problem !== null || value === exit} onClick={rename}>Rename</button>}
      </div>
      {problem !== null && <p className="wg-error-text">{problem}</p>}
      <h3>Outputs</h3>
      <FieldTableEditor rows={outputFields(doc, exit)} typeOptions={meta?.proto_types ?? []} readOnly={readOnly}
                        onChange={(rows) => design.apply(`edit exit ${exit}`, (d) => ({ ...d, process: setFieldMap(d.process, ["outputs", exit], rows) }))}
                        onRename={(from, to) => design.apply(`edit exit ${exit}`, (d) => ({ ...d, process: renameField(d.process, ["outputs", exit], from, to) }))} />
    </div>
  );
}

// `s:<name>`: header, actions, routing, interface, proto-step editor (`$DRAFTS/07 §7.7`).
import { useState, type ReactElement } from "react";
import { useDesign } from "../../api/context";
import { getIn, setIn } from "../../model/json";
import { edgeIndexFor, edgesOf, parseTarget, routeExit, setEntry, targetString } from "../../model/processDoc";
import { isDirty } from "../../state/design";
import { useOpenProcess } from "../../state/openProcess";
import { useUrlState } from "../../state/url";
import { protoSkeleton } from "../graph/AddStepDialog";
import { isReadOnly, protoPathOf, stepExits, useDesignState } from "../graph/designHooks";
import { RemoveStepDialog } from "../graph/RemoveStepDialog";
import { RenameStepDialog } from "../graph/RenameStepDialog";
import { targetOptions } from "./BranchCard";
import { ProtoStepEditor } from "./ProtoStepEditor";
import { SchemaSummary } from "./SchemaSummary";

export interface StepInspectorProps {
  step: string;
}

export function StepInspector({ step }: StepInspectorProps): ReactElement | null {
  const design = useDesign();
  const s = useDesignState();
  const [, setUrl] = useUrlState();
  const openProcess = useOpenProcess();
  const [dialog, setDialog] = useState<"rename" | "remove" | null>(null);
  if (s === null) return null;
  const doc = s.process.doc;
  const use = getIn(doc, ["steps", step, "use"]);
  const useText = typeof use === "string" ? use : "";
  const info = s.steps[step];
  const readOnly = isReadOnly(s);
  const protoPath = protoPathOf(s, step);
  const protoFile = protoPath === null ? undefined : s.protos[protoPath];
  const child = useText.startsWith("process:") ? useText.slice("process:".length) : null;
  const local = useText.startsWith("./steps/");
  const others = (info?.used_by ?? []).filter((p) => p !== s.processId);
  const declared = stepExits(s, step).filter((x) => x !== "error");
  const undeclared = edgesOf(doc).filter((e) => e.fromStep === step && e.fromExit !== "" && !declared.includes(e.fromExit)
    && e.fromExit !== "error").map((e) => e.fromExit);
  const exits = [...declared, "error", ...undeclared];

  function route(exit: string, value: string): void {
    if (value === "") return;
    const target = parseTarget(value);
    let sel = "";
    design.apply(`route ${step}.${exit} → ${value}`, (d) => {
      const r = routeExit(d.process, step, exit, target);
      sel = `b:${r.edgeIndex}:${r.branchIndex}`;
      return { ...d, process: r.doc };
    }, { structural: true });
    if (sel !== "") setUrl({ sel });
  }

  function createProto(): void {
    const name = useText.slice("./steps/".length);
    const path = s?.conventions.local_proto_path.replace("{name}", name);
    if (path === undefined) return;
    design.apply(`create proto-step ${name}`, (d) => ({ ...d, protos: { ...d.protos, [path]: protoSkeleton(name) } }));
  }

  return (
    <div className="wg-step-inspector">
      <header className="wg-inspector-head">
        <h2>{step}</h2>
        {!readOnly && <button type="button" onClick={() => setDialog("rename")}>Rename</button>}
      </header>
      <div className="wg-field">
        <label htmlFor="wg-step-use">use</label>
        <input id="wg-step-use" key={`${step}:${useText}`} className="wg-mono" defaultValue={useText} readOnly={readOnly}
               aria-describedby="wg-use-hint"
               onBlur={(e) => {
                 const next = e.target.value.trim();
                 if (next !== useText && next !== "") {
                   design.apply(`edit step ${step}`, (d) => ({ ...d, process: setIn(d.process, ["steps", step, "use"], next) }), { structural: true });
                 }
               }} />
        <small id="wg-use-hint" className="wg-muted">./steps/&lt;name&gt; · &lt;alias&gt;:&lt;path&gt; · process:&lt;id&gt;</small>
      </div>
      <div className="wg-badges">
        <span className="wg-badge">{info?.kind ?? "not compiled"}</span>
        <span className="wg-badge">{info?.phase ?? "missing"}</span>
        {info?.kind === "agentic" && info.lock !== null && (
          <span className="wg-badge">
            tier {info.lock.tier ?? "cheap"} · thinking {info.lock.thinking ?? "low"}
            {info.lock.provider !== null ? ` · ${info.lock.provider}` : ""}
          </span>
        )}
        {others.length > 0 && <span className="wg-chip">used by: {others.join(", ")}</span>}
      </div>
      {!readOnly && (
        <div className="wg-row">
          <button type="button" disabled={getIn(doc, ["entry"]) === step}
                  onClick={() => design.apply(`set entry ${step}`, (d) => ({ ...d, process: setEntry(d.process, step) }))}>
            Set as entry
          </button>
          <button type="button" className="wg-danger" onClick={() => setDialog("remove")}>Remove step</button>
        </div>
      )}

      <section aria-label="Routing">
        <h3>Routing</h3>
        <ul className="wg-routing">
          {exits.map((exit) => {
            const e = edgeIndexFor(doc, step, exit);
            const edge = e === -1 ? null : edgesOf(doc)[e];
            return (
              <li key={exit}>
                <span className="wg-mono">{exit}</span>
                {exit === "error" && e === -1 && <small className="wg-muted"> (implicit)</small>}
                {edge !== null && edge !== undefined ? (
                  <>
                    {" → "}{edge.branches.map((b) => targetString(b.target)).join(", ")}
                    <button type="button" onClick={() => setUrl({ sel: `b:${e}:0` })}>Edit</button>
                  </>
                ) : !readOnly && (
                  <select aria-label={`Route ${exit} to`} value="" onChange={(ev) => route(exit, ev.target.value)}>
                    <option value="">Route to…</option>
                    {targetOptions(doc).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                )}
              </li>
            );
          })}
        </ul>
      </section>

      <section aria-label="Interface">
        <h3>Interface</h3>
        <SchemaSummary iface={info?.interface ?? null} updating={protoFile !== undefined && isDirty(protoFile)} />
      </section>

      {child !== null && (
        <button type="button" onClick={() => void openProcess(child)}>Open process {child}</button>
      )}
      {protoPath !== null && info?.phase === "compiled" && (
        <p className="wg-hint">Editing the proto-step makes this step design-phase until recompiled.</p>
      )}
      {protoPath !== null && <ProtoStepEditor step={step} protoPath={protoPath} />}
      {protoPath === null && local && !readOnly && (
        <button type="button" onClick={createProto}>Create proto-step</button>
      )}
      {dialog === "rename" && <RenameStepDialog open step={step} onClose={() => setDialog(null)} />}
      {dialog === "remove" && <RemoveStepDialog open step={step} onClose={() => setDialog(null)} />}
    </div>
  );
}

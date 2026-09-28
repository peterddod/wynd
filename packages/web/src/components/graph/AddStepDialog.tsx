// New proto-step / existing step / another process (`$DRAFTS/07 §7.6`). Reference cycles are reported by the
// validator in the save response; the dialog does not pre-check them.
import { useState, type ReactElement } from "react";
import { useApi, useDesign } from "../../api/context";
import type { Json } from "../../api/types";
import { addStep, stepNames } from "../../model/processDoc";
import { useQuery } from "../../state/query";
import { useUrlState } from "../../state/url";
import { Dialog } from "../common/Dialog";
import { stepNameProblem, useDesignState } from "./designHooks";

export interface AddStepDialogProps {
  open: boolean;
  onClose(): void;
  onAdded?(step: string): void;      // e.g. retarget a branch to the new step ("New step…")
}

type Source = "new" | "existing" | "process";

interface Choice {
  use: string;
  label: string;
  detail: string;
}

export function protoSkeleton(name: string): Json {
  return { kind: "proto_step", name, instruction: "", exits: ["done"], examples: [] };
}

export function AddStepDialog({ open, onClose, onAdded }: AddStepDialogProps): ReactElement | null {
  const api = useApi();
  const design = useDesign();
  const s = useDesignState();
  const [, setUrl] = useUrlState();
  const [name, setName] = useState("");
  const [source, setSource] = useState<Source>("new");
  const [protoName, setProtoName] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);
  const catalog = useQuery("steps", () => api.steps.list(), { enabled: open && source === "existing" });
  const processes = useQuery("processes?&", () => api.processes.list("", []), { enabled: open && source === "process" });
  if (s === null) return null;

  const problem = stepNameProblem(name, stepNames(s.process.doc));
  const proto = protoName ?? name;
  const choices: Choice[] = source === "existing"
    ? [
        ...s.availableLocal.map((a) => ({ use: a.use, label: a.use, detail: a.phase })),
        ...(catalog.data?.steps ?? []).map((c) => ({ use: c.use, label: c.use, detail: `${c.phase}${c.instruction ? ` · ${c.instruction}` : ""}` })),
      ]
    : (processes.data?.processes ?? []).filter((p) => p.id !== s.processId)
        .map((p) => ({ use: `process:${p.id}`, label: p.id, detail: p.goal ?? "" }));
  const shown = choices.filter((c) => `${c.label} ${c.detail}`.toLowerCase().includes(filter.toLowerCase()));
  const use = source === "new" ? s.conventions.local_use.replace("{name}", proto) : picked;
  const ready = problem === null && use !== null && (source !== "new" || stepNameProblem(proto, []) === null);

  function close(): void {
    setName("");
    setSource("new");
    setProtoName(null);
    setFilter("");
    setPicked(null);
    setTouched(false);
    onClose();
  }

  function add(): void {
    setTouched(true);
    if (!ready || use === null || s === null) return;
    const path = s.conventions.local_proto_path.replace("{name}", proto);
    const createProto = source === "new" && (s.protos[path] === undefined || s.protos[path]?.deleted === true);
    design.apply(`add step ${name}`, (d) => ({
      process: addStep(d.process, name, use),
      protos: createProto ? { ...d.protos, [path]: protoSkeleton(proto) } : d.protos,
    }), { structural: true });
    if (onAdded !== undefined) onAdded(name);
    else setUrl({ sel: `s:${name}` });
    close();
  }

  return (
    <Dialog open={open} title="Add a step" onClose={close} className="wg-dialog"
            actions={<>
              <button type="button" onClick={close}>Cancel</button>
              <button type="button" onClick={add} disabled={touched && !ready}>Add step</button>
            </>}>
      <label className="wg-field">
        <span>Step name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} aria-invalid={touched && problem !== null}
               aria-describedby="wg-add-step-problem" autoFocus />
      </label>
      {touched && problem !== null && <p id="wg-add-step-problem" className="wg-error-text">{problem}</p>}
      <fieldset className="wg-field">
        <legend>Source</legend>
        {([["new", "New proto-step"], ["existing", "Existing step"], ["process", "Another process"]] as const).map(([value, label]) => (
          <label key={value}>
            <input type="radio" name="wg-add-step-source" value={value} checked={source === value}
                   onChange={() => { setSource(value); setPicked(null); }} />
            {label}
          </label>
        ))}
      </fieldset>
      {source === "new" && (
        <label className="wg-field">
          <span>Proto-step name</span>
          <input value={proto} onChange={(e) => setProtoName(e.target.value)} />
          <small>Creates {s.conventions.local_proto_path.replace("{name}", proto || "…")}</small>
        </label>
      )}
      {source !== "new" && (
        <>
          <label className="wg-field">
            <span>Search</span>
            <input value={filter} onChange={(e) => setFilter(e.target.value)} />
          </label>
          <ul className="wg-choices" aria-label={source === "existing" ? "Steps" : "Processes"}>
            {shown.map((c) => (
              <li key={c.use}>
                <label>
                  <input type="radio" name="wg-add-step-choice" checked={picked === c.use} onChange={() => setPicked(c.use)} />
                  <span className="wg-mono">{c.label}</span> <small>{c.detail}</small>
                </label>
              </li>
            ))}
            {shown.length === 0 && <li className="wg-muted">{catalog.loading || processes.loading ? "Loading…" : "Nothing to choose from."}</li>}
          </ul>
        </>
      )}
    </Dialog>
  );
}

// One branch: order, name, target, condition/else, with, limits, extra keys; "Check (agentic)" + context chips only
// when the edge kind is agentic (`$DRAFTS/07 §7.7`; PLAN §10 amendment 2). Every control applies a `processDoc` op
// labelled "edit branch <branch key>".
import { useState, type ReactElement } from "react";
import { useDesign } from "../../api/context";
import type { Json } from "../../api/types";
import {
  type BranchPatch, type BranchView, branchKey, type EdgeView, IGNORE, moveBranch, normalizedLoc, parseTarget, processExits,
  removeBranch, setBranchExtra, stepNames, targetString, updateBranch,
} from "../../model/processDoc";
import { useUrlState } from "../../state/url";
import { AddStepDialog } from "../graph/AddStepDialog";
import { classes, targetFields, useDesignState } from "../graph/designHooks";
import { ExpressionInput } from "./ExpressionInput";
import { ExtraFieldsEditor } from "./ExtraFieldsEditor";
import { LimitsEditor } from "./LimitsEditor";
import { WithMappingEditor } from "./WithMappingEditor";

export interface BranchCardProps {
  edge: EdgeView;
  branch: BranchView;
  expanded: boolean;
  onCycle: boolean;
  readOnly: boolean;
}

const NEW_STEP = "__new_step__";

/** Every routable target of the open process: steps, declared `$exit.<name>`s and `$ignore`. */
export function targetOptions(doc: Json): { value: string; label: string }[] {
  return [
    ...stepNames(doc).map((s) => ({ value: s, label: s })),
    ...processExits(doc).map((x) => ({ value: `$exit.${x}`, label: `$exit.${x}` })),
    { value: IGNORE, label: `${IGNORE} (explicitly ignored)` },
  ];
}

function summary(b: BranchView): string {
  const cond = b.when !== null ? `if ${b.when}` : b.check !== null ? `check: ${b.check}` : "else";
  return `${cond} → ${targetString(b.target)}`;
}

export function BranchCard({ edge, branch, expanded, onCycle, readOnly }: BranchCardProps): ReactElement | null {
  const design = useDesign();
  const s = useDesignState();
  const [, setUrl] = useUrlState();
  const [adding, setAdding] = useState(false);
  const [context, setContext] = useState("");
  if (s === null) return null;
  const e = edge.index;
  const b = branch.index;
  const key = branchKey(edge, b);
  const label = `edit branch ${key}`;
  const count = edge.branches.length;

  function update(patch: BranchPatch, structural = false): void {
    design.apply(label, (d) => ({ ...d, process: updateBranch(d.process, e, b, patch) }), { structural });
  }

  function move(to: number): void {
    design.apply(`move branch ${key}`, (d) => ({ ...d, process: moveBranch(d.process, e, b, to) }), { structural: true });
    setUrl({ sel: `b:${e}:${to}` });
  }

  function remove(): void {
    design.apply(`remove branch ${key}`, (d) => ({ ...d, process: removeBranch(d.process, e, b) }), { structural: true });
    setUrl({ sel: count > 1 ? `b:${e}:${Math.max(0, b - 1)}` : null });
  }

  if (!expanded) {
    return (
      <li className={classes("wg-branch-card", "wg-collapsed", branch.ignored && "wg-ignored")}>
        <button type="button" aria-expanded={false} onClick={() => setUrl({ sel: `b:${e}:${b}` })}>
          {b + 1}. {summary(branch)}
        </button>
      </li>
    );
  }

  const target = targetString(branch.target);
  const options = targetOptions(s.process.doc);
  const agentic = edge.kind === "agentic";
  return (
    <li className={classes("wg-branch-card", branch.ignored && "wg-ignored")} aria-label={`Branch ${b + 1}`}>
      <div className="wg-branch-head">
        <strong>Branch {b + 1}</strong>
        {!readOnly && (
          <>
            <button type="button" aria-label="Move branch up" disabled={b === 0} onClick={() => move(b - 1)}>↑</button>
            <button type="button" aria-label="Move branch down" disabled={b === count - 1} onClick={() => move(b + 1)}>↓</button>
          </>
        )}
        {branch.isElse && <span className="wg-chip">else</span>}
        {branch.ignored && <span className="wg-chip wg-chip-muted">ignored (after else)</span>}
        {onCycle && <span className="wg-chip">on a cycle</span>}
      </div>
      <label className="wg-field">
        <span>Name</span>
        <input value={branch.name ?? ""} readOnly={readOnly} placeholder={`optional, for edges["${edge.from}"].<name>.taken`}
               onChange={(ev) => update({ name: ev.target.value === "" ? null : ev.target.value })} />
      </label>
      <label className="wg-field">
        <span>Target</span>
        <select value={target} disabled={readOnly}
                onChange={(ev) => {
                  if (ev.target.value === NEW_STEP) setAdding(true);
                  else update({ target: parseTarget(ev.target.value) }, true);
                }}>
          {!options.some((o) => o.value === target) && <option value={target}>{target} (unknown)</option>}
          {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          <option value={NEW_STEP}>New step…</option>
        </select>
      </label>
      <label className="wg-check">
        <input type="checkbox" checked={branch.when === null} disabled={readOnly}
               onChange={(ev) => update({ when: ev.target.checked ? null : "" })} />
        Otherwise (else)
      </label>
      {branch.when !== null && (
        <ExpressionInput id={`wg-when-${e}-${b}`} label="Condition" value={branch.when} loc={normalizedLoc(e, b, "when")}
                         readOnly={readOnly} autoFocus={branch.when === ""} onChange={(v) => update({ when: v })} />
      )}
      {agentic && (
        <div className="wg-check-fields">
          <label className="wg-field">
            <span>Check (agentic)</span>
            <textarea value={branch.check ?? ""} readOnly={readOnly} placeholder="A condition in plain language, checked by a model"
                      onChange={(ev) => update({ check: ev.target.value === "" ? null : ev.target.value })} />
          </label>
          <div className="wg-field">
            <span>Context</span>
            <ul className="wg-chips" aria-label="Check context">
              {(branch.context ?? []).map((c, i) => (
                <li key={`${c}-${i}`} className="wg-chip wg-mono">
                  {c}
                  {!readOnly && (
                    <button type="button" aria-label={`Remove context ${c}`}
                            onClick={() => {
                              const next = (branch.context ?? []).filter((_, j) => j !== i);
                              update({ context: next.length === 0 ? null : next });
                            }}>×</button>
                  )}
                </li>
              ))}
            </ul>
            {!readOnly && (
              <div className="wg-row">
                <input aria-label="New context entry" className="wg-mono" placeholder="previous.outputs" value={context}
                       onChange={(ev) => setContext(ev.target.value)} />
                <button type="button" disabled={context.trim() === ""}
                        onClick={() => { update({ context: [...(branch.context ?? []), context.trim()] }); setContext(""); }}>
                  Add context
                </button>
              </div>
            )}
          </div>
        </div>
      )}
      <WithMappingEditor e={e} b={b} targetFields={targetFields(s, branch.target)} readOnly={readOnly} />
      <LimitsEditor e={e} b={b} onCycle={onCycle} readOnly={readOnly} />
      <ExtraFieldsEditor label="Other fields" value={branch.extra} readOnly={readOnly}
                         onChange={(next) => design.apply(label, (d) => ({ ...d, process: setBranchExtra(d.process, e, b, next) }))} />
      {!readOnly && <button type="button" className="wg-danger" onClick={remove}>Remove branch</button>}
      <AddStepDialog open={adding} onClose={() => setAdding(false)}
                     onAdded={(step) => update({ target: { type: "step", step } }, true)} />
    </li>
  );
}

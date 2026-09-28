// One step run, collapsed/expanded, children nested (`$DRAFTS/07 §11.5`). A running step is expanded until the
// user toggles it.
import { useId, useState, type ReactElement } from "react";
import { isTrace, type TraceEvent } from "../../api/types";
import type { StepRun } from "../../model/trace";
import { JsonView } from "../common/JsonView";
import { formatCost, formatDuration, formatUsage } from "./format";

export interface StepRunRowProps {
  run: StepRun;
  depth: number;
}

export function StepRunRow({ run, depth }: StepRunRowProps): ReactElement {
  const [toggled, setToggled] = useState<boolean | null>(null);
  const expanded = toggled ?? run.status === "running";
  const detailsId = useId();
  const outcome = run.status === "running" ? "running…" : run.exit ?? "timed out";
  return (
    <li className={`wo-step wo-step-${run.status}`}>
      <button
        type="button"
        className="wo-step-head"
        style={{ paddingLeft: `${depth * 1.25 + 0.5}rem` }}
        aria-expanded={expanded}
        aria-controls={detailsId}
        onClick={() => setToggled(!expanded)}
      >
        <span className="wo-step-path">{run.step}</span>
        {run.run > 1 && <span className="wo-muted">run {run.run}</span>}
        {run.attempts !== null && run.attempts > 1 && <span className="wo-muted">{run.attempts} attempts</span>}
        {run.kind === "process" && <span className="wo-tag">process: {run.id.replace(/^process:/, "")}</span>}
        {run.role !== "node" && <span className="wo-tag">{run.role}</span>}
        <span className={`wo-pill wo-exit-${run.status}`}>{outcome}</span>
        <span className="wo-muted">{formatDuration(run.durationMs)}</span>
        {run.usage?.cost_usd != null && <span className="wo-muted">{formatCost(run.usage.cost_usd)}</span>}
        {run.edge !== null && (
          <span className="wo-step-edge">→ {run.edge.to} via {run.edge.from}[{run.edge.name ?? run.edge.branch}]</span>
        )}
      </button>
      {expanded && <StepDetails id={detailsId} run={run} />}
      {run.children.length > 0 && (
        <ul className="wo-steps">
          {run.children.map((child) => <StepRunRow key={child.key} run={child} depth={depth + 1} />)}
        </ul>
      )}
    </li>
  );
}

function StepDetails({ id, run }: { id: string; run: StepRun }): ReactElement {
  const models = run.events.filter((e) => e.type === "model.call").length;
  const tools = run.events.filter((e) => e.type === "tool.call").length;
  return (
    <div id={id} className="wo-step-details">
      <dl className="wo-facts">
        <dt>Step</dt><dd><code>{run.id}</code>{run.kind !== null && ` · ${run.kind}`}</dd>
        {run.via !== null && <><dt>Entered via</dt><dd>{run.via}</dd></>}
        {run.model !== null && (
          <><dt>Model</dt><dd>{run.model.provider}/{run.model.model_id}{run.model.tier !== null && ` · ${run.model.tier}`}{run.model.thinking !== null && ` · thinking ${run.model.thinking}`}</dd></>
        )}
        {run.usage !== null && <><dt>Usage</dt><dd>{formatUsage(run.usage)}</dd></>}
      </dl>
      <h4>Inputs</h4>
      <JsonView value={run.inputs} label="Inputs" collapsed={1} />
      {run.status !== "running" && (
        <>
          <h4>Outputs</h4>
          <JsonView value={run.outputs} label="Outputs" collapsed={1} />
        </>
      )}
      {run.summary !== null && (
        <>
          <h4>Summary</h4>
          <JsonView value={run.summary.key_outputs} label="Key outputs" collapsed={1} />
          {run.summary.note !== "" && <p>{run.summary.note}</p>}
        </>
      )}
      {run.events.length > 0 && (
        <>
          <h4>Activity <span className="wo-muted">({models} model call(s), {tools} tool call(s))</span></h4>
          <ul className="wo-events">
            {run.events.map((ev) => <li key={ev.seq}>{eventLine(ev)}</li>)}
          </ul>
        </>
      )}
    </div>
  );
}

/** One line per event, matching `wynd trace` wording where there is one ("check: NOT TAKEN"). */
export function eventLine(ev: TraceEvent): string {
  if (isTrace(ev, "model.call")) {
    const parts = [`model ${ev.provider}/${ev.model_id}`, ev.outcome];
    if (ev.usage !== null) parts.push(`${ev.usage.input_tokens} in / ${ev.usage.output_tokens} out`, formatCost(ev.usage.cost_usd));
    parts.push(ev.cassette);
    return parts.filter((p) => p !== "").join(" · ");
  }
  if (isTrace(ev, "tool.call")) {
    return `tool ${ev.tool} (${ev.source}) · ${ev.ok ? "ok" : `error: ${ev.error ?? "tool error"}`} · ${formatDuration(ev.latency_ms)}`;
  }
  if (isTrace(ev, "step.log")) return `[${ev.level}] ${ev.message}`;
  if (isTrace(ev, "edge.check")) {
    const verdict = ev.take === true ? "TAKEN" : ev.take === false ? "NOT TAKEN" : `failed (${ev.error_cause ?? "error"})`;
    return `check ${ev.branch_key}: ${verdict}${ev.reason ? ` — ${ev.reason}` : ""}`;
  }
  if (isTrace(ev, "worker.start")) return `worker ${ev.venv} (pid ${ev.pid}) started in ${formatDuration(ev.startup_ms)}`;
  if (isTrace(ev, "step.event")) return `event ${ev.name}`;
  return ev.type;
}

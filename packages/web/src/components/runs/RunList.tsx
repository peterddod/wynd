// Runs of the process, polled every 3 s while any is queued/running (`$DRAFTS/07 §11.1`).
import type { ReactElement } from "react";
import { useApi } from "../../api/context";
import { useQuery } from "../../state/query";
import { ErrorBox } from "../common/ErrorBox";
import { formatDuration, formatTime, isLive, runOutcome, targetLabel } from "./format";

export const RUN_POLL_MS = 3000;

export interface RunListProps {
  processId: string;
  selected: string | null;
  onSelect(runId: string): void;
  onNew(): void;
}

export function RunList({ processId, selected, onSelect, onNew }: RunListProps): ReactElement {
  const api = useApi();
  const runs = useQuery(`runs:${processId}`, () => api.runs.list({ process_id: processId }), {
    pollMs: (data) => (data?.runs.some(isLive) === true ? RUN_POLL_MS : null),
  });
  const list = runs.data?.runs;
  return (
    <section className="wo-run-list" aria-label="Runs">
      <button type="button" className="wo-new" aria-pressed={selected === null} onClick={onNew}>New run</button>
      <ErrorBox error={runs.error} onRetry={() => void runs.reload()} />
      {list !== undefined && list.length === 0 && <p className="wo-muted">No runs yet.</p>}
      {list !== undefined && list.length > 0 && (
        <ul className="wo-rows">
          {list.map((run) => (
            <li key={run.id}>
              <button
                type="button"
                className="wo-row wo-run-row"
                aria-current={run.id === selected ? "true" : undefined}
                aria-label={`Run ${run.id}: ${runOutcome(run)}`}
                onClick={() => onSelect(run.id)}
              >
                <span className={`wo-pill wo-outcome-${run.status}`}>{runOutcome(run)}</span>
                <span className="wo-muted">{run.trigger} · {targetLabel(run.target, run.commit)}</span>
                <span>{formatTime(run.started_at)}</span>
                <span className="wo-muted">{formatDuration(run.duration_ms)}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

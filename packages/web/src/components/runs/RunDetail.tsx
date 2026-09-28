// Run header, outputs / ProcessErrorView, and the SSE trace tree (`$DRAFTS/07 §11.4`). The stream replays every
// persisted event from seq 0, then follows live and ends with `end`; a reconnect resumes from Last-Event-ID.
import { useEffect, useRef, useState, type ReactElement } from "react";
import { useApi } from "../../api/context";
import { openEventStream } from "../../api/sse";
import type { Run, TraceEvent } from "../../api/types";
import { applyTraceEvent, emptyTree, type RunTree } from "../../model/trace";
import { invalidate, useQuery } from "../../state/query";
import { ErrorBox } from "../common/ErrorBox";
import { JsonView } from "../common/JsonView";
import { formatDuration, formatTime, formatUsage, runOutcome, shortSha, targetLabel } from "./format";
import { ProcessErrorView } from "./ProcessErrorView";
import { StepRunRow } from "./StepRunRow";

export interface RunDetailProps {
  runId: string;
}

type StreamState = "connecting" | "open" | "reconnecting" | "ended";

export function RunDetail({ runId }: RunDetailProps): ReactElement {
  const api = useApi();
  const run = useQuery(`run:${runId}`, () => api.runs.get(runId));
  const [tree, setTree] = useState<RunTree>(emptyTree);
  const [stream, setStream] = useState<StreamState>("connecting");
  const reload = useRef(run.reload);
  useEffect(() => {
    reload.current = run.reload;
  });

  useEffect(() => {
    let seen = 0;
    setTree(emptyTree());
    setStream("connecting");
    return openEventStream(api.runs.eventsUrl(runId, 0), {
      events: {
        trace: (data) => {
          const ev = data as TraceEvent;
          if (ev.seq <= seen) return;
          seen = ev.seq;
          setTree((t) => applyTraceEvent(t, ev));
        },
        end: () => {
          setStream("ended");
          invalidate("runs:");
          void reload.current();
        },
      },
      onOpen: () => setStream((s) => (s === "ended" ? s : "open")),
      onError: () => setStream((s) => (s === "ended" ? s : "reconnecting")),
    });
  }, [api, runId]);

  const record = run.data;
  return (
    <article className="wo-run-detail" aria-label={`Run ${runId}`}>
      <ErrorBox error={run.error} onRetry={() => void run.reload()} />
      {record !== undefined && <RunHeader run={record} />}
      {stream === "reconnecting" && <span className="wo-pill wo-reconnecting" role="status">reconnecting…</span>}
      {record?.exit === "error" && record.error !== null ? (
        <ProcessErrorView error={record.error} />
      ) : (
        record?.outputs != null && (
          <section className="wo-section">
            <h3>Outputs</h3>
            <JsonView value={record.outputs} label="Outputs" />
          </section>
        )
      )}
      <section className="wo-section" aria-label="Trace">
        <h3>Trace</h3>
        {tree.roots.length === 0 ? (
          <p className="wo-muted">{stream === "ended" ? "No steps ran." : "Waiting for trace events…"}</p>
        ) : (
          <ul className="wo-steps">
            {tree.roots.map((r) => <StepRunRow key={r.key} run={r} depth={0} />)}
          </ul>
        )}
      </section>
    </article>
  );
}

function RunHeader({ run }: { run: Run }): ReactElement {
  return (
    <header className="wo-run-header">
      <h2>
        <span className={`wo-pill wo-outcome-${run.status}`} role="status" aria-live="polite">{runOutcome(run)}</span>{" "}
        <code>{run.id}</code>
      </h2>
      <dl className="wo-facts">
        <dt>Status</dt><dd>{run.status}</dd>
        <dt>Mode</dt><dd>{run.mode}</dd>
        <dt>Target</dt><dd>{targetLabel(run.target, run.commit)}</dd>
        <dt>Commit</dt><dd><code>{shortSha(run.commit)}</code></dd>
        <dt>Trigger</dt><dd>{run.trigger}</dd>
        <dt>Started</dt><dd>{formatTime(run.started_at)}</dd>
        <dt>Duration</dt><dd>{formatDuration(run.duration_ms)}</dd>
        {run.usage !== null && <><dt>Usage</dt><dd>{formatUsage(run.usage)}</dd></>}
      </dl>
    </header>
  );
}

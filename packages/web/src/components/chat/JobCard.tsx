// Job card: header, logs, compile session, build result, integration, error (`$DRAFTS/07 §9.7`). The job comes from
// the `job:<id>` query, polled 1 s while active and 5 s while awaiting answers; JobWatcher and the answer cards write
// the same cache entry, so a visible card follows them without extra requests.
import { useState, type ReactElement } from "react";
import { useApi } from "../../api/context";
import type { Job, JobError, JobKind } from "../../api/types";
import { JOB_KIND_LABEL, JOB_STATUS_LABEL, isActive, jobPollMs } from "../../state/jobs";
import { setQueryData, useQuery } from "../../state/query";
import { toast } from "../../state/toasts";
import { ErrorBox } from "../common/ErrorBox";
import { Icon, type IconName } from "../common/Icon";
import { Spinner } from "../common/Spinner";
import { BuildResult } from "./BuildResult";
import { CompileSessionView } from "./CompileSessionView";
import { IntegrationOutcome } from "./IntegrationOutcome";
import { JobLogs } from "./JobLogs";

export interface JobCardProps {
  jobId: string;
}

const KIND_ICON: Record<JobKind, IconName> = {
  compile: "compile", test_live: "test", build: "build", bake: "build", optimise: "settings",
};

/** "42s", "6m 29s", "1h 05m" from started_at (else created_at) to finished_at (else now). */
export function elapsed(job: Job, now: number): string {
  const start = Date.parse(job.started_at ?? job.created_at);
  const end = job.finished_at === null ? now : Date.parse(job.finished_at);
  const s = Math.max(0, Math.round((end - start) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
}

function JobErrorView({ error }: { error: JobError }): ReactElement {
  return (
    <div className="wc-job-error" role="alert">
      <p>{error.message}</p>
      {error.detail !== null && error.detail !== "" && (
        <details>
          <summary>Details</summary>
          <pre>{error.detail}</pre>
        </details>
      )}
    </div>
  );
}

export function JobCard({ jobId }: JobCardProps): ReactElement {
  const api = useApi();
  const query = useQuery<Job>(`job:${jobId}`, () => api.jobs.get(jobId), { pollMs: jobPollMs });
  const [logsOpen, setLogsOpen] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const job = query.data;

  if (job === undefined) {
    return (
      <section className="wc-job" aria-label="Job">
        {query.error !== null
          ? <ErrorBox error={query.error} onRetry={() => void query.reload()} />
          : <Spinner label="Loading job" />}
      </section>
    );
  }

  const cancel = async (): Promise<void> => {
    setCancelling(true);
    try {
      setQueryData(`job:${job.id}`, await api.jobs.cancel(job.id));
    } catch (err) {
      toast(`Could not cancel the job: ${err instanceof Error ? err.message : String(err)}`, { level: "error" });
    } finally {
      setCancelling(false);
    }
  };

  const kind = JOB_KIND_LABEL[job.kind];
  const cancellable = isActive(job) || job.status === "awaiting_input";
  return (
    <section className="wc-job" aria-label={`${kind} job for ${job.process_id}`} data-status={job.status}>
      <header className="wc-job-head">
        <Icon name={KIND_ICON[job.kind]} />
        <span className="wc-job-title">
          {kind} <strong>{job.process_id}</strong> at <code title={job.ref}>{job.ref.slice(0, 7)}</code>
        </span>
        <span className="wc-pill" data-status={job.status}>{JOB_STATUS_LABEL[job.status]}</span>
        <span className="wc-muted">{elapsed(job, Date.now())}</span>
        {job.usage.cost_usd !== null && <span className="wc-muted">${job.usage.cost_usd.toFixed(2)}</span>}
        <span className="wc-job-actions">
          {cancellable && (
            <button type="button" disabled={cancelling} onClick={() => void cancel()}>Cancel</button>
          )}
          <button type="button" aria-expanded={logsOpen} onClick={() => setLogsOpen(!logsOpen)}>
            Logs {logsOpen ? "▾" : "▸"}
          </button>
        </span>
      </header>
      {logsOpen && <JobLogs jobId={job.id} active={isActive(job)} />}
      {job.session !== null && <CompileSessionView job={job} session={job.session} />}
      {job.build !== null && <BuildResult job={job} build={job.build} />}
      {job.integration !== null && <IntegrationOutcome integration={job.integration} />}
      {job.error !== null && <JobErrorView error={job.error} />}
    </section>
  );
}

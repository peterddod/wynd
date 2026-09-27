// JobWatcher, mounted once in App: polls active jobs, integrates finished compile/test_live jobs (`$DRAFTS/07 §9.9`).
// Integration applies to every commit-producing kind (PLAN §3.18: compile, test_live, optimise), matching the
// controller's `GET /api/jobs?active=true`, which lists those jobs until they are integrated.
import { useEffect, useRef } from "react";
import { ApiError, type Api } from "../api/client";
import { useApi, useDesign } from "../api/context";
import type { Job, JobKind, JobStatus } from "../api/types";
import type { DesignSession } from "./design";
import { invalidate, setQueryData, useQuery } from "./query";
import { toast } from "./toasts";
import { useUrlState, type SetUrl } from "./url";

export const ACTIVE_POLL_MS = 2000;
export const IDLE_POLL_MS = 15000;
export const JOB_POLL_MS = 1000;            // a job card while its job is queued or running
export const AWAITING_POLL_MS = 5000;       // a job card while its job awaits answers

const COMMIT_KINDS: readonly JobKind[] = ["compile", "test_live", "optimise"];

export const JOB_KIND_LABEL: Record<JobKind, string> = {
  compile: "Compile", test_live: "Live test", build: "Build", bake: "Bake", optimise: "Optimise",
};

export const JOB_STATUS_LABEL: Record<JobStatus, string> = {
  queued: "queued", running: "running", awaiting_input: "awaiting input", succeeded: "succeeded", failed: "failed",
  cancelled: "cancelled",
};

/** queued | running */
export function isActive(job: Job): boolean {
  return job.status === "queued" || job.status === "running";
}

/** succeeded commit-producing job not yet integrated. */
export function needsIntegration(job: Job): boolean {
  return job.status === "succeeded" && job.integration === null && COMMIT_KINDS.includes(job.kind);
}

/** Poll interval of one job's card: 1 s while active, 5 s while awaiting answers, else none. */
export function jobPollMs(job: Job | undefined): number | null {
  if (job === undefined) return null;
  if (isActive(job)) return JOB_POLL_MS;
  if (job.status === "awaiting_input") return AWAITING_POLL_MS;
  return null;
}

function openProcessId(design: DesignSession): string | null {
  return design.store.get()?.processId ?? null;
}

function dirtyPaths(err: unknown): string[] {
  if (!(err instanceof ApiError)) return [];
  const paths = (err.details as { paths?: unknown } | null)?.paths;
  return Array.isArray(paths) ? paths.map(String) : [];
}

interface WatchDeps {
  api: Api;
  design: DesignSession;
  setUrl: SetUrl;
}

async function integrate(deps: WatchDeps, job: Job): Promise<void> {
  const { api, design } = deps;
  const isOpen = (): boolean => openProcessId(design) === job.process_id;
  try {
    if (isOpen()) await design.commit("before_integrate");
    let done: Job;
    try {
      done = await api.jobs.integrate(job.id);
    } catch (err) {
      if (!(err instanceof ApiError && err.code === "dirty_tree")) throw err;
      if (isOpen()) await design.commit("before_integrate");
      done = await api.jobs.integrate(job.id);
    }
    setQueryData(`job:${job.id}`, done);
    invalidate("processes");
    if (isOpen()) await design.reload();
  } catch (err) {
    const paths = dirtyPaths(err);
    const why = paths.length > 0
      ? `uncommitted changes in ${paths.join(", ")}`
      : err instanceof Error ? err.message : String(err);
    toast(`Could not integrate ${JOB_KIND_LABEL[job.kind].toLowerCase()} of ${job.process_id}: ${why}`, {
      level: "error",
    });
  }
}

/** A job that left the active list has finished: react to how it ended. */
function finished(deps: WatchDeps, job: Job): void {
  if (job.status === "succeeded" && job.kind === "build") {
    invalidate(`builds:${job.process_id}`);
    invalidate("processes");
  }
  if (job.status === "failed") {
    const chatId = job.chat_id;
    const reason = job.error === null ? "" : `: ${job.error.message}`;
    toast(`${JOB_KIND_LABEL[job.kind]} of ${job.process_id} failed${reason}`, {
      level: "error",
      action: chatId === null ? undefined : { label: "Open chat", run: () => deps.setUrl({ chat: chatId }) },
    });
  }
}

export function JobWatcher(): null {
  const api = useApi();
  const design = useDesign();
  const [, setUrl] = useUrlState();
  const active = useQuery("jobs:active", () => api.jobs.list({ active: true }), {
    pollMs: (d) => (d !== undefined && d.jobs.length > 0 ? ACTIVE_POLL_MS : IDLE_POLL_MS),
  });
  const listed = useRef(new Set<string>());          // ids in the previous active list
  const integrating = useRef(new Set<string>());     // integrated (or tried) once per page life
  const latest = useRef<WatchDeps>({ api, design, setUrl });

  useEffect(() => {
    latest.current = { api, design, setUrl };
  });

  useEffect(() => {
    const jobs = active.data?.jobs;
    if (jobs === undefined) return;
    const deps = latest.current;
    const now = new Set(jobs.map((j) => j.id));
    const gone = [...listed.current].filter((id) => !now.has(id));
    listed.current = now;
    for (const job of jobs) setQueryData(`job:${job.id}`, job);
    for (const job of jobs) {
      if (!needsIntegration(job) || integrating.current.has(job.id)) continue;
      integrating.current.add(job.id);
      void integrate(deps, job);
    }
    for (const id of gone) {
      deps.api.jobs.get(id).then(
        (job) => {
          setQueryData(`job:${id}`, job);
          finished(deps, job);
        },
        () => undefined,                             // deleted meanwhile: nothing to report
      );
    }
  }, [active.data]);

  return null;
}

// Submitting a compile or build job from the UI (`$DRAFTS/07 §6.3`): used by ProcessActions and NewReleaseDialog.
import { ApiError, type Api } from "../../api/client";
import type { JobWithChat } from "../../api/types";
import type { DesignSession } from "../../state/design";
import { invalidate, setQueryData } from "../../state/query";

export type JobButton = "compile" | "build";

/** Commits the open design ("before_job"), submits the job and seeds its query; the caller opens `chat`. */
export async function startProcessJob(api: Api, design: DesignSession, pid: string, kind: JobButton): Promise<JobWithChat> {
  await design.commit("before_job");
  const started = kind === "compile" ? await api.processes.compile(pid) : await api.processes.build(pid);
  setQueryData(`job:${started.job.id}`, started.job);
  invalidate("jobs");
  invalidate("chats");
  return started;
}

/** `details.paths` of a 409 `dirty_tree`, else null. */
export function dirtyPaths(err: unknown): string[] | null {
  if (!(err instanceof ApiError) || err.code !== "dirty_tree") return null;
  const paths = (err.details as { paths?: unknown } | null)?.paths;
  return Array.isArray(paths) ? paths.map(String) : [];
}

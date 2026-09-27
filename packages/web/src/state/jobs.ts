// JobWatcher, mounted once in App: polls active jobs, integrates finished compile/test_live jobs (`$DRAFTS/07 §9.9`).
// Stub from WEB-SCAFFOLD; WEB-CHAT implements it.
import type { Job } from "../api/types";

export const ACTIVE_POLL_MS = 2000;
export const IDLE_POLL_MS = 15000;

/** queued | running */
export function isActive(job: Job): boolean {
  throw new Error("not implemented");
}

/** succeeded compile/test_live job not yet integrated. */
export function needsIntegration(job: Job): boolean {
  throw new Error("not implemented");
}

export function JobWatcher(): null {
  return null;
}

// Switching the open process: commit the current one first, then open the next (`$DRAFTS/07 §8.5`).
// Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { DesignSession } from "./design";
import type { SetUrl } from "./url";

export interface OpenProcessDeps {
  design: DesignSession;
  setUrl: SetUrl;
}

/** Commits the current process ("process_switch"), sets `?process=` (push, clearing sel/run) and opens `next`. */
export async function openProcess(deps: OpenProcessDeps, next: string | null): Promise<void> {
  throw new Error("not implemented");
}

/** The switch-blocked dialog: [Retry] [Discard my changes and switch]. */
export function showSwitchBlocked(error: unknown, retry: () => void, discard: () => void): void {
  throw new Error("not implemented");
}

/** `openProcess` bound to the app's design session and URL state. */
export function useOpenProcess(): (next: string | null) => Promise<void> {
  throw new Error("not implemented");
}

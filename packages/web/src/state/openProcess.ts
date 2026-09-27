// Switching the open process: commit the current one first, then open the next (`$DRAFTS/07 §8.5`). A failed commit
// blocks the switch with a native dialog offering [Retry] and [Discard my changes and switch]; it is built with DOM
// calls so it works wherever openProcess is called from, without a mounted React host.
import { useCallback } from "react";
import { ApiError } from "../api/client";
import { useDesign } from "../api/context";
import type { DesignSession } from "./design";
import { toast } from "./toasts";
import { useUrlState, type SetUrl } from "./url";

export interface OpenProcessDeps {
  design: DesignSession;
  setUrl: SetUrl;
}

/** Commits the current process ("process_switch"), sets `?process=` (push, clearing sel/run) and opens `next`. */
export async function openProcess(deps: OpenProcessDeps, next: string | null): Promise<void> {
  const cur = deps.design.store.get();
  if (cur !== null && cur.processId !== next) {
    try {
      await deps.design.commit("process_switch");
    } catch (e) {
      showSwitchBlocked(e, () => void openProcess(deps, next), () => {
        deps.design.close();
        void openProcess(deps, next);
      });
      return;
    }
  }
  deps.setUrl({ process: next, sel: null, run: null }, "push");
  if (next === null) {
    deps.design.close();
    return;
  }
  try {
    await deps.design.open(next);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      toast(`Process ${next} no longer exists`, { level: "warning" });
      deps.setUrl({ process: null, sel: null, run: null }, "replace");
      return;
    }
    toast(`Could not open ${next}: ${e instanceof Error ? e.message : String(e)}`, { level: "error" });
  }
}

export const SWITCH_BLOCKED_CLASS = "wg-switch-blocked";

/** The switch-blocked dialog: [Retry] [Discard my changes and switch]. */
export function showSwitchBlocked(error: unknown, retry: () => void, discard: () => void): void {
  document.querySelector(`dialog.${SWITCH_BLOCKED_CLASS}`)?.remove();
  const dialog = document.createElement("dialog");
  dialog.className = SWITCH_BLOCKED_CLASS;
  dialog.setAttribute("aria-labelledby", "wg-switch-blocked-title");
  const title = document.createElement("h2");
  title.id = "wg-switch-blocked-title";
  title.textContent = "Your changes could not be committed";
  const text = document.createElement("p");
  const message = error instanceof Error ? error.message : String(error);
  text.textContent = `Committing the open process failed before switching: ${message}`;
  const actions = document.createElement("div");
  const button = (label: string, run: () => void): HTMLButtonElement => {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.addEventListener("click", () => {
      dialog.remove();
      run();
    });
    return b;
  };
  actions.append(button("Retry", retry), button("Discard my changes and switch", discard));
  dialog.append(title, text, actions);
  dialog.addEventListener("cancel", () => dialog.remove());
  document.body.append(dialog);
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

/** `openProcess` bound to the app's design session and URL state. */
export function useOpenProcess(): (next: string | null) => Promise<void> {
  const design = useDesign();
  const [, setUrl] = useUrlState();
  return useCallback((next: string | null) => openProcess({ design, setUrl }, next), [design, setUrl]);
}

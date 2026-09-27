// Focus boundary of the design editors (`$DRAFTS/07 §8.3`). Focus leaving the surface after an edit commits
// ("blur"); moving between inputs inside it does not. The tab going hidden commits ("hidden"); page unload sends a
// keepalive commit ("unload"); window focus reloads a clean design. Clicks on non-focusable parts of the surface
// focus the surface itself, so they keep focus inside. App wraps the graph (GraphEditor + Inspector) and the
// process-settings tab in it, with a ConflictBanner; the surface shows the assistant-lock notice.
import { useEffect, useRef, type FocusEvent, type PointerEvent, type ReactElement, type ReactNode } from "react";
import { useDesign } from "../../api/context";
import { anyDirty } from "../../state/design";
import { classes, useDesignState } from "./designHooks";

export interface DesignSurfaceProps {
  children?: ReactNode;
  className?: string;
}

const FOCUSABLE = "input, textarea, select, button, a[href], [tabindex], [contenteditable]";

export function DesignSurface({ children, className }: DesignSurfaceProps): ReactElement | null {
  const design = useDesign();
  const s = useDesignState();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const pending = (): boolean => {
      const cur = design.store.get();
      return cur !== null && (anyDirty(cur) || cur.uncommitted);
    };
    const onVisibility = (): void => {
      if (document.visibilityState === "hidden" && pending()) void design.commit("hidden").catch(() => undefined);
    };
    const onPageHide = (): void => {
      if (pending()) void design.commit("unload").catch(() => undefined);
    };
    const onFocus = (): void => void design.reload().catch(() => undefined);
    const onBeforeUnload = (e: BeforeUnloadEvent): void => {
      const state = design.store.get()?.saveState;
      if (state === "error" || state === "conflict") e.preventDefault();
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onPageHide);
    window.addEventListener("focus", onFocus);
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onPageHide);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, [design]);

  function onBlurCapture(e: FocusEvent<HTMLDivElement>): void {
    const next = e.relatedTarget as Node | null;
    if (next !== null && ref.current?.contains(next) === true) return;
    const cur = design.store.get();
    if (cur === null || !(anyDirty(cur) || cur.uncommitted)) return;
    void design.commit("blur").catch(() => undefined);
  }

  function onPointerDown(e: PointerEvent<HTMLDivElement>): void {
    const hit = (e.target as HTMLElement).closest(FOCUSABLE);
    if (hit === null || hit === ref.current) ref.current?.focus({ preventScroll: true });
  }

  const locked = s?.lockedBy !== null && s?.lockedBy !== undefined;
  return (
    <div ref={ref} tabIndex={-1} className={classes("wg-design-surface", className)} onBlurCapture={onBlurCapture}
         onPointerDown={onPointerDown}>
      {locked && <div className="wg-banner wg-banner-locked" role="status">Assistant is working on this process…</div>}
      {children}
    </div>
  );
}

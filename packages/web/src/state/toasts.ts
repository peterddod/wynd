// Toasts rendered in an aria-live="polite" region (`$DRAFTS/07 §4.6`). Info and warning toasts dismiss themselves
// after TOAST_MS; errors stay until dismissed. At most MAX_TOASTS are kept (oldest dropped).
import { createStore, useStore } from "./store";

export type ToastLevel = "info" | "warning" | "error";

export interface ToastAction {
  label: string;
  run(): void;
}

export interface Toast {
  id: number;
  level: ToastLevel;
  text: string;
  action: ToastAction | null;
}

export const TOAST_MS = 6000;
export const MAX_TOASTS = 5;

const store = createStore<Toast[]>([]);
let nextId = 1;

/** Shows a toast; returns its id. */
export function toast(text: string, opts?: { level?: ToastLevel; action?: ToastAction }): number {
  const id = nextId++;
  const level = opts?.level ?? "info";
  store.set((toasts) => [...toasts, { id, level, text, action: opts?.action ?? null }].slice(-MAX_TOASTS));
  if (level !== "error") setTimeout(() => dismissToast(id), TOAST_MS);
  return id;
}

export function dismissToast(id: number): void {
  store.set((toasts) => (toasts.some((t) => t.id === id) ? toasts.filter((t) => t.id !== id) : toasts));
}

/** Removes every toast (tests). */
export function clearToasts(): void {
  store.set([]);
}

export function useToasts(): Toast[] {
  return useStore(store, (toasts) => toasts);
}

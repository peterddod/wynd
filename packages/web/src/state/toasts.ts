// Toasts rendered in an aria-live="polite" region (`$DRAFTS/07 §4.6`). Stub from WEB-SCAFFOLD; WEB-CORE implements it.

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

/** Shows a toast; returns its id. */
export function toast(text: string, opts?: { level?: ToastLevel; action?: ToastAction }): number {
  throw new Error("not implemented");
}

export function dismissToast(id: number): void {
  throw new Error("not implemented");
}

export function useToasts(): Toast[] {
  throw new Error("not implemented");
}

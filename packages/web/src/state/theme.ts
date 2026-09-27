// Theme: system -> light -> dark, kept in localStorage["wynd.theme"] and applied as
// document.documentElement.dataset.theme (removed for system) (`$DRAFTS/07 §4.7`).
// Stub from WEB-SCAFFOLD; WEB-CORE implements it.

export type Theme = "system" | "light" | "dark";

export const THEME_KEY = "wynd.theme";

/** The stored theme; "system" when unset or storage is unavailable. */
export function readTheme(): Theme {
  throw new Error("not implemented");
}

export function applyTheme(theme: Theme): void {
  throw new Error("not implemented");
}

export function nextTheme(theme: Theme): Theme {
  throw new Error("not implemented");
}

export function useTheme(): [Theme, (next: Theme) => void] {
  throw new Error("not implemented");
}

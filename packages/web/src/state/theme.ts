// Theme: system -> light -> dark, kept in localStorage["wynd.theme"] and applied as
// document.documentElement.dataset.theme (removed for system) (`$DRAFTS/07 §4.7`). Storage is a per-viewer
// convenience: every access is guarded and the page works without it.
import { createStore, useStore, type Store } from "./store";

export type Theme = "system" | "light" | "dark";

export const THEME_KEY = "wynd.theme";

let store: Store<Theme> | null = null;

/** The stored theme; "system" when unset or storage is unavailable. */
export function readTheme(): Theme {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") delete root.dataset.theme;
  else root.dataset.theme = theme;
}

export function nextTheme(theme: Theme): Theme {
  switch (theme) {
    case "system":
      return "light";
    case "light":
      return "dark";
    case "dark":
      return "system";
  }
}

function themeStore(): Store<Theme> {
  if (store === null) store = createStore<Theme>(readTheme());
  return store;
}

/** Stores, applies and publishes the theme. */
export function setTheme(theme: Theme): void {
  try {
    if (theme === "system") window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    // storage unavailable: the choice lasts for this page only
  }
  applyTheme(theme);
  themeStore().set(theme);
}

export function useTheme(): [Theme, (next: Theme) => void] {
  return [useStore(themeStore(), (t) => t), setTheme];
}

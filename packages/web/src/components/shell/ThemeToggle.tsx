// Cycles system -> light -> dark (`$DRAFTS/07 §4.7`).
import type { ReactElement } from "react";
import { nextTheme, useTheme, type Theme } from "../../state/theme";
import { Icon } from "../common/Icon";

const LABELS: Record<Theme, string> = { system: "System", light: "Light", dark: "Dark" };

export function ThemeToggle(): ReactElement {
  const [theme, setTheme] = useTheme();
  const next = nextTheme(theme);
  const label = `Theme: ${LABELS[theme]} (switch to ${LABELS[next]})`;
  return (
    <button type="button" className="wy-theme-toggle" aria-label={label} title={label} onClick={() => setTheme(next)}>
      <Icon name="theme" />
      <span aria-hidden="true">{LABELS[theme]}</span>
    </button>
  );
}

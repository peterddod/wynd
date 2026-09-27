// Inline SVG icon set (`$DRAFTS/07 §16`). Stub from WEB-SCAFFOLD; WEB-CORE implements it.
import type { ReactElement } from "react";

export type IconName =
  | "read" | "write" | "check" | "cross" | "warning" | "info" | "copy" | "plus" | "close" | "trash" | "edit"
  | "up" | "down" | "chevron-right" | "chevron-down" | "search" | "settings" | "theme" | "menu" | "chat"
  | "compile" | "build" | "test" | "release" | "run" | "process" | "step" | "link" | "upload" | "stop";

export interface IconProps {
  name: IconName;
  title?: string;                    // accessible name; decorative (aria-hidden) when absent
  size?: number;                     // px, default 16
}

export function Icon(_props: IconProps): ReactElement | null {
  return null;
}

// Inline SVG icon set (`$DRAFTS/07 §16`): 24×24 strokes in currentColor, so icons follow the text colour and theme.
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

type Shape =
  | { d: string; fill?: true }
  | { circle: [number, number, number]; fill?: true }
  | { rect: [number, number, number, number, number]; fill?: true };

const X: Shape[] = [{ d: "M18 6 6 18M6 6l12 12" }];
const DOWN: Shape[] = [{ d: "m6 9 6 6 6-6" }];

export const ICONS: Record<IconName, Shape[]> = {
  read: [{ d: "M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" }, { circle: [12, 12, 3] }],
  write: [{ d: "M12 20h9" }, { d: "M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" }],
  check: [{ d: "M20 6 9 17l-5-5" }],
  cross: X,
  warning: [{ d: "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" }, { d: "M12 9v4M12 17h.01" }],
  info: [{ circle: [12, 12, 10] }, { d: "M12 16v-4M12 8h.01" }],
  copy: [{ rect: [9, 9, 13, 13, 2] }, { d: "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" }],
  plus: [{ d: "M12 5v14M5 12h14" }],
  close: X,
  trash: [{ d: "M3 6h18M8 6V4h8v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6" }],
  edit: [{ d: "M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" }, { d: "M18.5 2.5a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4z" }],
  up: [{ d: "m18 15-6-6-6 6" }],
  down: DOWN,
  "chevron-right": [{ d: "m9 18 6-6-6-6" }],
  "chevron-down": DOWN,
  search: [{ circle: [11, 11, 7] }, { d: "m21 21-4.3-4.3" }],
  settings: [{ d: "M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6" }],
  theme: [{ circle: [12, 12, 9] }, { d: "M12 3a9 9 0 0 1 0 18z", fill: true }],
  menu: [{ d: "M3 6h18M3 12h18M3 18h18" }],
  chat: [{ d: "M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" }],
  compile: [{ d: "m16 18 6-6-6-6M8 6l-6 6 6 6" }],
  build: [
    { d: "M21 16V8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4a2 2 0 0 0 1-1.7z" },
    { d: "M3.3 7 12 12l8.7-5M12 22V12" },
  ],
  test: [{ d: "M9 3h6M10 3v6L4.3 19a2 2 0 0 0 1.7 3h12a2 2 0 0 0 1.7-3L14 9V3M7 15h10" }],
  release: [{ d: "m22 2-11 11M22 2l-7 20-4-9-9-4z" }],
  run: [{ d: "M6 4l14 8-14 8z" }],
  process: [{ circle: [5, 6, 2.5] }, { circle: [19, 6, 2.5] }, { circle: [12, 18, 2.5] }, { d: "M7.5 6h9M6.3 8.2l4.4 7.6M17.7 8.2l-4.4 7.6" }],
  step: [{ rect: [3, 3, 18, 18, 3] }],
  link: [
    { d: "M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" },
    { d: "M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" },
  ],
  upload: [{ d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" }],
  stop: [{ rect: [6, 6, 12, 12, 1.5], fill: true }],
};

function shape(s: Shape, key: number): ReactElement {
  const fill = s.fill === true ? "currentColor" : undefined;
  if ("d" in s) return <path key={key} d={s.d} fill={fill} />;
  if ("circle" in s) return <circle key={key} cx={s.circle[0]} cy={s.circle[1]} r={s.circle[2]} fill={fill} />;
  const [x, y, width, height, rx] = s.rect;
  return <rect key={key} x={x} y={y} width={width} height={height} rx={rx} fill={fill} />;
}

export function Icon({ name, title, size = 16 }: IconProps): ReactElement {
  return (
    <svg
      className={`wy-icon wy-icon-${name}`}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title === undefined ? undefined : "img"}
      aria-label={title}
      aria-hidden={title === undefined ? true : undefined}
      focusable="false"
    >
      {title !== undefined && <title>{title}</title>}
      {ICONS[name].map(shape)}
    </svg>
  );
}

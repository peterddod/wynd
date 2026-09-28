// Per-browser node position overrides in localStorage["wynd.layout.<workspace root>.<pid>"] (`$DRAFTS/07 §7.4`);
// never written to process.yaml. Keys are node ids ("s:<step>", "x:<exit>", "in"). Storage can be missing or throw
// (private windows, blocked site data): every access is guarded and the editor falls back to the automatic layout.

export type XY = { x: number; y: number };

export function layoutKey(root: string, pid: string): string {
  return `wynd.layout.${root}.${pid}`;
}

/** {} when unset or storage is unavailable. */
export function loadOverrides(root: string, pid: string): Record<string, XY> {
  try {
    const raw = window.localStorage.getItem(layoutKey(root, pid));
    const parsed: unknown = raw === null ? {} : JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    const out: Record<string, XY> = {};
    for (const [id, v] of Object.entries(parsed as Record<string, unknown>)) {
      const p = v as Partial<XY> | null;
      if (typeof p?.x === "number" && typeof p.y === "number") out[id] = { x: p.x, y: p.y };
    }
    return out;
  } catch {
    return {};
  }
}

function store(root: string, pid: string, overrides: Record<string, XY>): void {
  try {
    if (Object.keys(overrides).length === 0) window.localStorage.removeItem(layoutKey(root, pid));
    else window.localStorage.setItem(layoutKey(root, pid), JSON.stringify(overrides));
  } catch {
    // storage unavailable: the override lives only until the next layout
  }
}

export function saveOverride(root: string, pid: string, node: string, pos: XY): void {
  store(root, pid, { ...loadOverrides(root, pid), [node]: { x: pos.x, y: pos.y } });
}

export function clearOverrides(root: string, pid: string): void {
  store(root, pid, {});
}

/** Migrates a step's override after `renameStep`. */
export function renameOverride(root: string, pid: string, from: string, to: string): void {
  const current = loadOverrides(root, pid);
  const pos = current[`s:${from}`];
  if (pos === undefined) return;
  const { [`s:${from}`]: _, ...rest } = current;
  store(root, pid, { ...rest, [`s:${to}`]: pos });
}

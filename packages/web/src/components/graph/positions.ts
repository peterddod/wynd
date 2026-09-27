// Per-browser node position overrides in localStorage["wynd.layout.<workspace root>.<pid>"] (`$DRAFTS/07 §7.4`);
// never written to process.yaml. Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.

export type XY = { x: number; y: number };

export function layoutKey(root: string, pid: string): string {
  throw new Error("not implemented");
}

/** {} when unset or storage is unavailable. */
export function loadOverrides(root: string, pid: string): Record<string, XY> {
  throw new Error("not implemented");
}

export function saveOverride(root: string, pid: string, node: string, pos: XY): void {
  throw new Error("not implemented");
}

export function clearOverrides(root: string, pid: string): void {
  throw new Error("not implemented");
}

/** Migrates a step's override after `renameStep`. */
export function renameOverride(root: string, pid: string, from: string, to: string): void {
  throw new Error("not implemented");
}

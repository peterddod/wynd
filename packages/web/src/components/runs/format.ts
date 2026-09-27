// Display helpers shared by the runs, releases and process components (WEB-OPS).
import type { JsonSchema, Run, RunTarget, Usage } from "../../api/types";

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return "";
  return `$${usd < 0.01 ? usd.toFixed(4) : usd.toFixed(2)}`;
}

export function formatUsage(u: Usage): string {
  const parts = [`${u.input_tokens} in / ${u.output_tokens} out tokens`, `${u.calls} call(s)`];
  if (u.cost_usd !== null) parts.push(formatCost(u.cost_usd));
  if (u.latency_ms > 0) parts.push(`model ${formatDuration(u.latency_ms)}`);
  return parts.join(" · ");
}

export function formatTime(iso: string | null | undefined): string {
  if (iso === null || iso === undefined) return "–";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function shortSha(sha: string | null | undefined): string {
  return sha === null || sha === undefined ? "–" : sha.slice(0, 7);
}

export function targetLabel(t: RunTarget, commit?: string | null): string {
  switch (t.kind) {
    case "local":
      return "local";
    case "image":
      return `image ${shortSha(t.commit ?? commit)}`;
    case "release":
      return "release";
  }
}

/** The run's outcome as one word: its exit once finished, else its status. */
export function runOutcome(run: Run): string {
  return run.exit ?? run.status;
}

export function isLive(run: Run): boolean {
  return run.status === "queued" || run.status === "running";
}

/** `format: "path"` on the field or one of its `anyOf` alternatives (optional fields). */
export function isPathSchema(s: JsonSchema | undefined | null): boolean {
  if (s === undefined || s === null) return false;
  return s.format === "path" || (s.anyOf ?? []).some(isPathSchema);
}

export function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

// Status badges: pure display of controller-derived flags (`$DRAFTS/07 §6.2`). Every flag is its own badge and every
// badge carries text, so a process released at X and in design at HEAD shows both.
import type { ProcessStatus } from "../api/types";

export interface Badge {
  kind: "design" | "compiled" | "built" | "released" | "tests_failed";
  label: string;
  title: string;
}

export function statusBadges(s: ProcessStatus): Badge[] {
  const head = s.head === null ? "HEAD" : `HEAD ${s.head.short}`;
  const badges: Badge[] = [];
  if (s.design) {
    const n = s.design_steps.length;
    badges.push({
      kind: "design",
      label: "design",
      title: `${n} step(s) have proto-steps with no matching compiled source at HEAD: ${s.design_steps.join(", ")}`,
    });
  }
  if (s.compiled) {
    badges.push({ kind: "compiled", label: "compiled", title: `Compiled source matches proto hashes and tests pass at ${head}` });
  }
  if (!s.compiled && !s.design && s.tests === "failed") {
    badges.push({ kind: "tests_failed", label: "tests failing", title: `Tests fail at ${head}` });
  }
  if (s.built) {
    badges.push({ kind: "built", label: "built", title: `Build artefact exists for ${head}` });
  }
  for (const r of s.releases) {
    const at = r.behind === 0 ? "HEAD" : `${r.behind} behind`;
    badges.push({
      kind: "released",
      label: `released ${r.short} · ${at}`,
      title: `Release ${r.id} at ${r.commit}: ${r.trigger} trigger, ${r.state}`,
    });
  }
  return badges;
}

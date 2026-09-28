import { describe, expect, it } from "vitest";
import type { ProcessStatus } from "../api/types";
import { fixture } from "../test/fixtures";
import { statusBadges } from "./status";

function statusOf(id: string): ProcessStatus {
  const status = fixture("processesList").find((p) => p.id === id)?.status;
  if (status == null) throw new Error(`no status for ${id}`);
  return status;
}

describe("statusBadges", () => {
  it("design and a release behind HEAD show side by side", () => {
    const badges = statusBadges(statusOf("process_supplier_invoice"));
    expect(badges.map((b) => [b.kind, b.label])).toEqual([
      ["design", "design"],
      ["released", "released 9f8e7d6 · 3 behind"],
    ]);
    expect(badges[0]?.title).toBe("1 step(s) have proto-steps with no matching compiled source at HEAD: fix");
    expect(badges[1]?.title).toContain("schedule trigger, serving");
  });

  it("compiled and built flags each get a badge naming the HEAD commit", () => {
    const badges = statusBadges(statusOf("finance/monthly_close"));
    expect(badges.map((b) => b.kind)).toEqual(["compiled", "built"]);
    expect(badges[0]?.title).toBe("Compiled source matches proto hashes and tests pass at HEAD 7a6b5c4");
    expect(badges[1]?.title).toBe("Build artefact exists for HEAD 7a6b5c4");
  });

  it("a release at HEAD shows '· HEAD'", () => {
    const status = statusOf("process_supplier_invoice");
    status.releases = status.releases.map((r) => ({ ...r, behind: 0 }));
    const released = statusBadges(status).filter((b) => b.kind === "released");
    expect(released.map((b) => b.label)).toEqual(["released 9f8e7d6 · HEAD"]);
  });

  it("one badge per release", () => {
    const status = statusOf("process_supplier_invoice");
    const first = status.releases[0]!;
    status.releases.push({ ...first, id: "rel_2", short: "1234567", behind: 1, trigger: "webhook" });
    expect(statusBadges(status).filter((b) => b.kind === "released").map((b) => b.label))
      .toEqual(["released 9f8e7d6 · 3 behind", "released 1234567 · 1 behind"]);
  });

  it("tests failing only when neither design nor compiled", () => {
    const status = statusOf("finance/monthly_close");
    status.tests = "failed";
    status.compiled = false;
    expect(statusBadges(status).map((b) => [b.kind, b.label])).toEqual([["tests_failed", "tests failing"], ["built", "built"]]);
    status.design = true;
    status.design_steps = ["collect"];
    expect(statusBadges(status).map((b) => b.kind)).toEqual(["design", "built"]);
    status.design = false;
    status.compiled = true;
    expect(statusBadges(status).map((b) => b.kind)).toEqual(["compiled", "built"]);
  });

  it("no flags, no badges; a missing head still reads", () => {
    const status = statusOf("finance/monthly_close");
    expect(statusBadges({ ...status, compiled: false, built: false })).toEqual([]);
    expect(statusBadges({ ...status, head: null })[0]?.title).toBe("Compiled source matches proto hashes and tests pass at HEAD");
  });
});

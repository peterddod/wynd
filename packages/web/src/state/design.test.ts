import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { JsonObject, SaveRequest } from "../api/types";
import { fireReconnect, invalidated, resetDoubles } from "../components/graph/testDoubles";
import { setIn } from "../model/json";
import { renameProcessExit, routeExit, updateBranch } from "../model/processDoc";
import { renameExit } from "../model/protoDoc";
import { createFakeApi, type FakeApi } from "../test/fakeApi";
import { fixture } from "../test/fixtures";
import { commitSummary, createDesignSession, DEBOUNCE_MS, type DesignSession } from "./design";

vi.mock("./store", () => import("../components/graph/testDoubles"));
vi.mock("./query", () => import("../components/graph/testDoubles"));
vi.mock("./connection", () => import("../components/graph/testDoubles"));

const PID = "process_supplier_invoice";
const PROCESS = "processes/process_supplier_invoice/process.yaml";
const EXTRACT = "processes/process_supplier_invoice/proto/extract_invoice_fields.yaml";

function saves(api: FakeApi): SaveRequest[] {
  return api.calls.filter((c) => c.method === "processes.save").map((c) => c.args[1] as SaveRequest);
}

function editWhen(design: DesignSession, when: string, label = "edit branch validate.done[1]"): void {
  design.apply(label, (d) => ({ ...d, process: updateBranch(d.process, 3, 1, { when }) }));
}

async function openSession(api: FakeApi = createFakeApi()): Promise<{ api: FakeApi; design: DesignSession }> {
  const design = createDesignSession(api);
  await design.open(PID);
  return { api, design };
}

beforeEach(() => {
  vi.useFakeTimers();
  resetDoubles();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("open", () => {
  it("loads the design into a clean state", async () => {
    const { design } = await openSession();
    const s = design.store.get()!;
    expect(s.processId).toBe(PID);
    expect(s.processPath).toBe(PROCESS);
    expect(Object.keys(s.protos)).toContain(EXTRACT);
    expect(s).toMatchObject({ saveState: "clean", uncommitted: false, issuesStale: false, lockedBy: null, parseError: null });
    expect(design.processDoc()).toBe(s.process.doc);
    expect(design.dirtyProtoDocs()).toEqual({});
  });
});

describe("autosave", () => {
  it("sends one save 800 ms after a burst of edits, never a commit", async () => {
    const { api, design } = await openSession();
    for (let i = 0; i < 10; i++) {
      editWhen(design, `steps.fix.runs < ${i}`);
      await vi.advanceTimersByTimeAsync(50);
    }
    expect(saves(api)).toHaveLength(0);
    expect(design.store.get()!.saveState).toBe("pending");
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    const sent = saves(api);
    expect(sent).toHaveLength(1);
    expect(sent[0]!.commit).toBeNull();
    expect(sent[0]!.writes).toEqual([{ path: PROCESS, base_revision: fixture("design").process_file.revision, doc: design.processDoc() }]);
    const s = design.store.get()!;
    expect(s).toMatchObject({ saveState: "clean", uncommitted: true });
    expect(s.process.savedDoc).toBe(s.process.doc);
    expect(s.process.revision).not.toBe(fixture("design").process_file.revision);
  });

  it("saves at the 5 s max wait while typing continues, then once more", async () => {
    const { api, design } = await openSession();
    for (let t = 0; t < 6000; t += 100) {
      editWhen(design, `steps.fix.runs < ${t}`);
      await vi.advanceTimersByTimeAsync(100);
    }
    expect(saves(api)).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(2000);
    expect(saves(api)).toHaveLength(2);
    expect(saves(api).every((r) => r.commit === null)).toBe(true);
  });

  it("sends the new base revision on the next save", async () => {
    const { api, design } = await openSession();
    editWhen(design, "a");
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    const revision = design.store.get()!.process.revision;
    editWhen(design, "b");
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(saves(api)[1]!.writes[0]!.base_revision).toBe(revision);
  });

  it("keeps edits made while a save is in flight dirty and saves them next", async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => (release = r));
    const api = createFakeApi();
    const real = vi.mocked(api.processes.save).getMockImplementation()!;
    vi.mocked(api.processes.save).mockImplementationOnce(async (...args) => {
      await gate;
      return real(...args);
    });
    const { design } = await openSession(api);
    editWhen(design, "first");
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(design.store.get()!.saveState).toBe("saving");
    editWhen(design, "second");
    release();
    await vi.advanceTimersByTimeAsync(0);
    const s = design.store.get()!;
    expect(s.saveState).toBe("pending");
    expect(s.process.doc).not.toBe(s.process.savedDoc);
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(saves(api)).toHaveLength(2);
    expect(design.store.get()!.saveState).toBe("clean");
  });
});

describe("commit", () => {
  it("sends writes and the commit in one request on blur", async () => {
    const { api, design } = await openSession();
    editWhen(design, "x");
    const result = await design.commit("blur");
    const sent = saves(api);
    expect(sent).toHaveLength(1);
    expect(sent[0]!.writes).toHaveLength(1);
    expect(sent[0]!.commit).toEqual({ reason: "blur", summary: "edit branch validate.done[1]" });
    expect(result).toEqual({ sha: fixture("saveResult").head });
    const s = design.store.get()!;
    expect(s).toMatchObject({ saveState: "clean", uncommitted: false, opLabels: [] });
    expect(s.lastCommit?.message).toBe(`design(${PID}): edit branch validate.done[1]`);
    expect(invalidated).toEqual(["processes"]);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(saves(api)).toHaveLength(1);
  });

  it("sends nothing when nothing is dirty or uncommitted", async () => {
    const { api, design } = await openSession();
    expect(await design.commit("blur")).toBeNull();
    expect(saves(api)).toHaveLength(0);
  });

  it("sends a commit-only request after an autosave", async () => {
    const { api, design } = await openSession();
    editWhen(design, "x");
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    await design.commit("hidden");
    const sent = saves(api);
    expect(sent).toHaveLength(2);
    expect(sent[1]).toEqual({ writes: [], commit: { reason: "hidden", summary: "edit branch validate.done[1]" } });
  });

  it("de-duplicates op labels and caps the summary", async () => {
    const { api, design } = await openSession();
    for (const label of ["a", "b", "a", "c", "d", "e", "f", "g"]) editWhen(design, label, label);
    await design.commit("before_job");
    expect(saves(api)[0]!.commit!.summary).toBe("a; b; c; d; e; +2 more");
    expect(commitSummary(["one"])).toBe("one");
    expect(commitSummary([])).toBe("edit design");
  });

  it("passes keepalive for the unload commit", async () => {
    const { api, design } = await openSession();
    editWhen(design, "x");
    await design.commit("unload");
    const call = api.calls.find((c) => c.method === "processes.save")!;
    expect(call.args[2]).toEqual({ keepalive: true });
  });
});

describe("files", () => {
  it("saves a proto exit rename and the process edge rename as one request with two writes", async () => {
    const { api, design } = await openSession();
    design.apply("rename exit not_an_invoice → other", (d) => ({
      process: setIn(d.process, ["edges", 2, "from"], "extract.other"),
      protos: { ...d.protos, [EXTRACT]: renameExit(d.protos[EXTRACT]!, "not_an_invoice", "other") },
    }), { structural: true });
    expect(design.dirtyProtoDocs()).toEqual({ [EXTRACT]: design.store.get()!.protos[EXTRACT]!.doc });
    await design.commit("blur");
    const sent = saves(api);
    expect(sent).toHaveLength(1);
    expect(sent[0]!.writes.map((w) => w.path).sort()).toEqual([PROCESS, EXTRACT].sort());
  });

  it("creates and deletes proto files", async () => {
    const { api, design } = await openSession();
    const path = "processes/process_supplier_invoice/proto/notify.yaml";
    design.apply("add step notify", (d) => ({ ...d, protos: { ...d.protos, [path]: { kind: "proto_step", name: "notify" } } }));
    design.apply("remove step fix", (d) => ({ ...d, protos: { ...d.protos, "processes/process_supplier_invoice/proto/fix_fields.yaml": null } }));
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    const writes = saves(api)[0]!.writes;
    expect(writes).toContainEqual({ path, base_revision: null, doc: { kind: "proto_step", name: "notify" } });
    expect(writes).toContainEqual({
      path: "processes/process_supplier_invoice/proto/fix_fields.yaml", base_revision: expect.stringMatching(/^sha256:/), delete: true,
    });
    const s = design.store.get()!;
    expect(s.protos[path]!.revision).toMatch(/^sha256:/);
    expect(s.protos["processes/process_supplier_invoice/proto/fix_fields.yaml"]).toBeUndefined();
  });

  it("drops a never-saved file that is deleted again", async () => {
    const { api, design } = await openSession();
    const path = "processes/process_supplier_invoice/proto/tmp.yaml";
    design.apply("add", (d) => ({ ...d, protos: { ...d.protos, [path]: { kind: "proto_step" } } }));
    design.apply("remove", (d) => ({ ...d, protos: { ...d.protos, [path]: null } }));
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(saves(api)).toHaveLength(0);
  });
});

describe("issues", () => {
  it("marks issues stale after a structural op until the next save", async () => {
    const { design } = await openSession();
    design.apply("route validate.done → save", (d) => ({ ...d, process: routeExit(d.process, "validate", "done", { type: "step", step: "save" }).doc }), { structural: true });
    expect(design.store.get()!.issuesStale).toBe(true);
    editWhen(design, "x");
    expect(design.store.get()!.issuesStale).toBe(true);
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(design.store.get()!.issuesStale).toBe(false);
    expect(design.store.get()!.report).toEqual(fixture("saveResult").validation);
  });
});

describe("errors", () => {
  it("keeps the docs on a 409 and resolves with the version on disk", async () => {
    const api = createFakeApi();
    vi.mocked(api.processes.save).mockRejectedValueOnce(new ApiError(409, "revision_conflict", "changed", { path: PROCESS, current_revision: "sha256:x" }));
    const { design } = await openSession(api);
    editWhen(design, "mine");
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    let s = design.store.get()!;
    expect(s.saveState).toBe("conflict");
    expect(updateBranch(s.process.doc, 3, 1, {})).toBe(s.process.doc);
    expect((s.process.doc as JsonObject).edges).not.toEqual((s.process.savedDoc as JsonObject).edges);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(saves(api)).toHaveLength(1);
    await design.resolveConflict("theirs");
    s = design.store.get()!;
    expect(s.saveState).toBe("clean");
    expect(s.process.doc).toEqual(fixture("design").process_file.doc);
    expect(saves(api)).toHaveLength(1);
  });

  it("resends mine with the server's revision", async () => {
    const api = createFakeApi();
    vi.mocked(api.processes.save).mockRejectedValueOnce(new ApiError(409, "revision_conflict", "changed", { path: PROCESS }));
    const server = fixture("design");
    server.process_file.revision = "sha256:server";
    const { design } = await openSession(api);
    vi.mocked(api.processes.design).mockResolvedValueOnce(server);
    editWhen(design, "mine");
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    await design.resolveConflict("mine");
    const sent = saves(api);
    expect(sent).toHaveLength(2);
    expect(sent[1]!.writes).toHaveLength(1);
    expect(sent[1]!.writes[0]!.base_revision).toBe("sha256:server");
    expect(sent[1]!.writes[0]!.doc).toEqual(updateBranch(fixture("design").process_file.doc, 3, 1, { when: "mine" }));
    expect(design.store.get()!.saveState).toBe("clean");
  });

  it("keeps the docs on a 500 and retries with backoff", async () => {
    const api = createFakeApi();
    vi.mocked(api.processes.save)
      .mockRejectedValueOnce(new ApiError(500, "internal", "boom"))
      .mockRejectedValueOnce(new ApiError(500, "internal", "boom"));
    const { design } = await openSession(api);
    editWhen(design, "x");
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(design.store.get()).toMatchObject({ saveState: "error", error: { code: "internal" } });
    await vi.advanceTimersByTimeAsync(1999);
    expect(saves(api)).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(saves(api)).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(5000);
    expect(saves(api)).toHaveLength(3);
    expect(design.store.get()).toMatchObject({ saveState: "clean", error: null, uncommitted: true });
  });

  it("retries immediately when the controller is reachable again", async () => {
    const api = createFakeApi();
    vi.mocked(api.processes.save).mockRejectedValueOnce(new ApiError(0, "network", "offline"));
    const { design } = await openSession(api);
    editWhen(design, "x");
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    fireReconnect();
    await vi.advanceTimersByTimeAsync(0);
    expect(saves(api)).toHaveLength(2);
    expect(design.store.get()!.saveState).toBe("clean");
  });

  it("rejects commit when the save fails", async () => {
    const api = createFakeApi();
    vi.mocked(api.processes.save).mockRejectedValueOnce(new ApiError(500, "internal", "boom"));
    const { design } = await openSession(api);
    editWhen(design, "x");
    await expect(design.commit("process_switch")).rejects.toThrow("boom");
  });
});

describe("lock", () => {
  it("makes apply a no-op while an assistant turn edits the process", async () => {
    const { api, design } = await openSession();
    design.lock({ chat_id: "chat_1", turn_id: "turn_1" });
    const before = design.store.get()!.process.doc;
    editWhen(design, "x");
    expect(design.store.get()!.process.doc).toBe(before);
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(saves(api)).toHaveLength(0);
    design.unlock("other_turn");
    expect(design.store.get()!.lockedBy).not.toBeNull();
    design.unlock("turn_1");
    expect(design.store.get()!.lockedBy).toBeNull();
  });

  it("reloads after the turn that edited the process ends", async () => {
    const api = createFakeApi();
    const { design } = await openSession(api);
    const edited = fixture("design");
    edited.process_file.revision = "sha256:after-chat";
    edited.process_file.doc = renameProcessExit(edited.process_file.doc, "needs_review", "escalated");
    vi.mocked(api.processes.design).mockResolvedValueOnce(edited);
    design.lock({ chat_id: "chat_1", turn_id: "turn_1" });
    design.unlock("turn_1");
    await design.reload();
    const s = design.store.get()!;
    expect(s.process.revision).toBe("sha256:after-chat");
    expect((s.process.doc as JsonObject).outputs).toHaveProperty("escalated");
    expect(s.saveState).toBe("clean");
  });

  it("takes the lock from a 423 and does not reload over dirty docs", async () => {
    const api = createFakeApi();
    vi.mocked(api.processes.save).mockRejectedValueOnce(new ApiError(423, "design_locked", "locked", { chat_id: "c", turn_id: "t" }));
    const { design } = await openSession(api);
    editWhen(design, "x");
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(design.store.get()!.lockedBy).toEqual({ chat_id: "c", turn_id: "t" });
    const doc = design.processDoc();
    await design.reload();
    expect(design.processDoc()).toBe(doc);
    expect(api.calls.filter((c) => c.method === "processes.design")).toHaveLength(1);
    design.unlock("t");
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    expect(saves(api)).toHaveLength(2);
  });
});

describe("close and parse errors", () => {
  it("drops the state on close and ignores a late save response", async () => {
    const { design } = await openSession();
    editWhen(design, "x");
    const pending = design.flush();
    design.close();
    await pending;
    expect(design.store.get()).toBeNull();
  });

  it("is read-only when process.yaml does not parse", async () => {
    const api = createFakeApi();
    const broken = fixture("design");
    broken.process_file.doc = null;
    broken.process_file.parse_error = { message: "mapping values are not allowed here", line: 3, column: 7 };
    vi.mocked(api.processes.design).mockResolvedValueOnce(broken);
    const { design } = await openSession(api);
    editWhen(design, "x");
    expect(design.store.get()!.saveState).toBe("clean");
    expect(design.store.get()!.parseError?.line).toBe(3);
  });
});

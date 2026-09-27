import { act, fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { FileDoc } from "../../api/types";
import type { DesignSession, DesignState, FileState } from "../../state/design";
import { createStore } from "../../state/store";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { StatusBar, saveStatus } from "./StatusBar";

function fileState(f: FileDoc): FileState {
  return { path: f.path, revision: f.revision, doc: f.doc, savedDoc: f.doc, yaml: f.yaml };
}

function designState(patch: Partial<DesignState> = {}): DesignState {
  const d = fixture("design");
  return {
    processId: d.process_id, processPath: d.process_file.path, process: fileState(d.process_file),
    protos: Object.fromEntries(Object.entries(d.protos).map(([path, f]) => [path, fileState(f)])),
    steps: d.steps, iface: d.interface, report: d.validation, issuesStale: false, conventions: d.conventions,
    availableLocal: d.available_local, parseError: null, saveState: "clean", uncommitted: false, opLabels: [],
    lockedBy: null, lastCommit: null, error: null, ...patch,
  };
}

function fakeDesign(state: DesignState | null): DesignSession {
  return {
    store: createStore(state), open: vi.fn(), apply: vi.fn(), flush: vi.fn(async () => undefined), commit: vi.fn(),
    reload: vi.fn(), resolveConflict: vi.fn(), lock: vi.fn(), unlock: vi.fn(), processDoc: vi.fn(),
    dirtyProtoDocs: vi.fn(), close: vi.fn(),
  };
}

describe("saveStatus", () => {
  it.each<[Partial<DesignState>, string, string]>([
    [{}, "Saved", "ok"],
    [{ lastCommit: { sha: "c3d4e5f60718", message: "design(p): x" } }, "Saved · committed c3d4e5f", "ok"],
    [{ lastCommit: { sha: "c3d4e5f60718", message: "m" }, uncommitted: true }, "Saved", "ok"],
    [{ saveState: "pending" }, "Unsaved changes", "busy"],
    [{ saveState: "saving" }, "Saving…", "busy"],
    [{ saveState: "error", error: new ApiError(500, "internal", "disk full") }, "Not saved: disk full", "err"],
    [{ saveState: "conflict" }, "Not saved: changed outside the editor", "warn"],
    [{ lockedBy: { chat_id: "c", turn_id: "t" }, saveState: "pending" }, "Assistant is working on this process…", "busy"],
    [{ parseError: { message: "bad", line: 3, column: 1 } }, "Read-only: process.yaml can't be parsed", "warn"],
  ])("%o -> %s", (patch, text, tone) => {
    expect(saveStatus(designState(patch))).toEqual({ text, tone });
  });
});

describe("StatusBar", () => {
  it("renders nothing while no process is open", () => {
    renderApp(<StatusBar />, { design: fakeDesign(null) });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("follows the design store in a status region", () => {
    const design = fakeDesign(designState({ saveState: "saving" }));
    renderApp(<StatusBar />, { design });
    expect(screen.getByRole("status").textContent).toContain("Saving…");
    act(() => design.store.set(designState({ lastCommit: { sha: "a1b2c3d4e5", message: "m" } })));
    expect(screen.getByRole("status").textContent).toContain("Saved · committed a1b2c3d");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("on a save error, Retry flushes the session", () => {
    const design = fakeDesign(designState({ saveState: "error", error: new ApiError(0, "network", "offline") }));
    vi.mocked(design.flush).mockRejectedValueOnce(new Error("still offline"));
    renderApp(<StatusBar />, { design });
    expect(screen.getByRole("status").textContent).toContain("Not saved: offline");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(design.flush).toHaveBeenCalledOnce();
  });
});

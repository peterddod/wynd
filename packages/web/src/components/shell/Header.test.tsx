import { act, fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ProcessStatus } from "../../api/types";
import type { DesignSession, DesignState } from "../../state/design";
import { invalidate, resetQueryCache } from "../../state/query";
import { createStore } from "../../state/store";
import { createFakeApi } from "../../test/fakeApi";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { CHAT_ID, Header } from "./Header";
import { SIDEBAR_ID } from "./Sidebar";

// WEB-OPS components are stood in by probes that show what the header passes them.
vi.mock("../process/StatusBadges", () => ({
  StatusBadges: ({ status }: { status: ProcessStatus | null }) => (
    <span data-testid="badges">{status === null ? "no status" : `design=${status.design} released=${status.released}`}</span>
  ),
}));
vi.mock("../process/ProcessActions", () => ({
  ProcessActions: ({ processId }: { processId: string }) => <span data-testid="actions">{processId}</span>,
}));
vi.mock("../registries/RegistriesDialog", () => ({
  RegistriesDialog: ({ open, onClose }: { open: boolean; onClose(): void }) =>
    open ? <button type="button" onClick={onClose}>Close settings</button> : null,
}));

function design(): DesignSession {
  return {
    store: createStore<DesignState | null>(null), open: vi.fn(), apply: vi.fn(), flush: vi.fn(), commit: vi.fn(), reload: vi.fn(),
    resolveConflict: vi.fn(), lock: vi.fn(), unlock: vi.fn(), processDoc: vi.fn(), dirtyProtoDocs: vi.fn(), close: vi.fn(),
  };
}

const PID = "process_supplier_invoice";

function renderHeader(url: { process?: string }, handlers = { onToggleSidebar: vi.fn(), onToggleChat: vi.fn() }) {
  const view = renderApp(<Header sidebarOpen chatOpen={false} {...handlers} />, { url, design: design() });
  return { ...view, ...handlers };
}

beforeEach(() => {
  resetQueryCache();
});

describe("Header", () => {
  it("without an open process: the brand only, no status, badges or actions", () => {
    const { api } = renderHeader({});
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Wynd");
    expect(screen.queryByTestId("badges")).toBeNull();
    expect(screen.queryByTestId("actions")).toBeNull();
    expect(api.processes.get).not.toHaveBeenCalled();
  });

  it("with an open process: its id, badges from its status, HEAD with the commit subject, and actions", async () => {
    const { api } = renderHeader({ process: PID });
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe(`Wynd▸${PID}`);
    expect(screen.getByTestId("actions").textContent).toBe(PID);
    expect(await screen.findByText("design=true released=true")).toBeTruthy();
    expect(api.processes.get).toHaveBeenCalledWith(PID);
    const head = screen.getByText("HEAD").closest(".wy-head");
    expect(head?.textContent).toBe("HEAD a1b2c3d");
    expect(head?.getAttribute("title")).toBe(
      "design(process_supplier_invoice): edit proto fix_fields (2026-09-27T09:58:12Z)",
    );
  });

  it("refreshes the status when processes are invalidated", async () => {
    const summary = fixture("processesList").find((p) => p.id === PID)!;
    const api = createFakeApi({ processes: { get: async () => summary } });
    renderApp(<Header sidebarOpen chatOpen onToggleSidebar={vi.fn()} onToggleChat={vi.fn()} />, {
      api, url: { process: PID }, design: design(),
    });
    await screen.findByText("design=true released=true");
    vi.mocked(api.processes.get).mockResolvedValueOnce({ ...summary, status: { ...summary.status!, design: false } });
    await act(async () => invalidate("processes"));
    expect(screen.getByText("design=false released=true")).toBeTruthy();
  });

  it("toggles report their state and call back", () => {
    const { onToggleSidebar, onToggleChat } = renderHeader({});
    const sidebar = screen.getByRole("button", { name: "Hide processes" });
    expect(sidebar.getAttribute("aria-expanded")).toBe("true");
    expect(sidebar.getAttribute("aria-controls")).toBe(SIDEBAR_ID);
    const chat = screen.getByRole("button", { name: "Show chat" });
    expect(chat.getAttribute("aria-expanded")).toBe("false");
    expect(chat.getAttribute("aria-controls")).toBe(CHAT_ID);
    fireEvent.click(sidebar);
    fireEvent.click(chat);
    expect(onToggleSidebar).toHaveBeenCalledOnce();
    expect(onToggleChat).toHaveBeenCalledOnce();
  });

  it("Settings opens the registries dialog; it closes itself", () => {
    renderHeader({});
    expect(screen.queryByRole("button", { name: "Close settings" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));
    fireEvent.click(screen.getByRole("button", { name: "Close settings" }));
    expect(screen.queryByRole("button", { name: "Close settings" })).toBeNull();
  });

  it("hosts the theme toggle", () => {
    renderHeader({});
    expect(screen.getByRole("button", { name: /^Theme:/ })).toBeTruthy();
  });
});

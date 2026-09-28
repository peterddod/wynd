// App shell tests. Every other unit's module in the App's import graph is replaced by a stand-in that shows what the
// shell passes it, so these tests exercise the shell alone (providers, layout, tabs, URL <-> design session,
// connection banner, theme, shortcuts), whatever state the other units' modules are in.
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ApiError } from "./api/client";
import { useDesign, useMeta } from "./api/context";
import type { FileDoc } from "./api/types";
import type { DesignSession, DesignState, FileState } from "./state/design";
import { resetConnection } from "./state/connection";
import { resetQueryCache } from "./state/query";
import { createStore, useStore } from "./state/store";
import { setTheme } from "./state/theme";
import { clearToasts } from "./state/toasts";
import { useUrlState } from "./state/url";
import { createFakeApi } from "./test/fakeApi";
import { fixture } from "./test/fixtures";
import { urlSearch } from "./test/render";

vi.mock("./state/design", () => ({
  createDesignSession: () => {
    throw new Error("the tests inject a design session");
  },
}));
vi.mock("./state/jobs", () => ({ JobWatcher: () => <span data-testid="job-watcher" /> }));
vi.mock("./components/process/ProcessSearch", () => ({ PROCESS_SEARCH_ID: "wynd-process-search" }));
vi.mock("./components/process/ProcessList", () => ({
  ProcessList: () => <input id="wynd-process-search" aria-label="Search processes" />,
}));
vi.mock("./components/process/StatusBadges", () => ({ StatusBadges: () => null }));
vi.mock("./components/process/ProcessActions", () => ({ ProcessActions: () => null }));
vi.mock("./components/registries/RegistriesDialog", () => ({ RegistriesDialog: () => null }));
vi.mock("./components/chat/ChatPanel", () => ({
  ChatPanel: ({ open }: { open: boolean }) => {
    const meta = useMeta();
    return <p data-testid="chat">{`${open ? "open" : "closed"} · llm ${meta?.llm.provider ?? "not loaded"}`}</p>;
  },
}));
vi.mock("./components/graph/DesignSurface", () => ({
  DesignSurface: ({ children }: { children?: ReactNode }) => <div data-testid="design-surface">{children}</div>,
}));
vi.mock("./components/graph/ConflictBanner", () => ({ ConflictBanner: () => null }));
vi.mock("./components/graph/GraphEditor", () => ({
  GraphEditor: () => {
    const design = useDesign();
    return <p>graph of {useStore(design.store, (s) => s?.processId ?? "nothing")}</p>;
  },
}));
vi.mock("./components/inspector/Inspector", () => ({ Inspector: () => <p>inspector</p> }));
vi.mock("./components/process-settings/ProcessSettings", () => ({ ProcessSettings: () => <p>process settings</p> }));
vi.mock("./components/yaml/YamlView", () => ({ YamlView: () => <p>yaml view</p> }));
vi.mock("./components/runs/RunPanel", () => ({
  RunPanel: ({ processId }: { processId: string }): ReactElement => {
    const [url] = useUrlState();
    return <p>runs of {processId}, run {url.run ?? "none"}</p>;
  },
}));
vi.mock("./components/releases/ReleasesPanel", () => ({
  ReleasesPanel: ({ processId }: { processId: string }) => <p>releases of {processId}</p>,
}));

const PID = "process_supplier_invoice";

function fileState(f: FileDoc): FileState {
  return { path: f.path, revision: f.revision, doc: f.doc, savedDoc: f.doc, yaml: f.yaml };
}

function designState(processId: string): DesignState {
  const d = fixture("design");
  return {
    processId, processPath: d.process_file.path, process: fileState(d.process_file),
    protos: Object.fromEntries(Object.entries(d.protos).map(([path, f]) => [path, fileState(f)])),
    steps: d.steps, iface: d.interface, report: d.validation, issuesStale: false, conventions: d.conventions,
    availableLocal: d.available_local, parseError: null, saveState: "clean", uncommitted: false, opLabels: [],
    lockedBy: null, lastCommit: null, error: null,
  };
}

/** A design session that knows two processes; `open` resolves when `release()` is called if `hold` is set. */
function fakeDesign(opts: { hold?: boolean } = {}) {
  const store = createStore<DesignState | null>(null);
  const known = new Set([PID, "finance/monthly_close"]);
  const held: (() => void)[] = [];
  const design: DesignSession = {
    store,
    open: vi.fn(async (pid: string) => {
      if (opts.hold === true) await new Promise<void>((resolve) => held.push(resolve));
      if (!known.has(pid)) throw new ApiError(404, "not_found", `process '${pid}' not found`);
      store.set(designState(pid));
    }),
    apply: vi.fn(), flush: vi.fn(async () => undefined), commit: vi.fn(async () => null), reload: vi.fn(async () => undefined),
    resolveConflict: vi.fn(async () => undefined), lock: vi.fn(), unlock: vi.fn(), processDoc: vi.fn(() => null),
    dirtyProtoDocs: vi.fn(() => ({})), close: vi.fn(() => store.set(null)),
  };
  return { design, release: async () => act(async () => held.shift()?.()) };
}

interface StartOpts {
  api?: ReturnType<typeof createFakeApi>;
  hold?: boolean;
  setup?(design: DesignSession): void;
}

function start(url: Parameters<typeof urlSearch>[0] = {}, opts: StartOpts = {}) {
  window.history.replaceState(null, "", `/${urlSearch(url)}`);
  const api = opts.api ?? createFakeApi();
  const { design, release } = fakeDesign({ hold: opts.hold });
  opts.setup?.(design);
  const view = render(<App api={api} design={design} />);
  return { ...view, api, design, release };
}

beforeEach(() => {
  resetQueryCache();
  clearToasts();
});

afterEach(() => {
  vi.useRealTimers();
  resetConnection();
  setTheme("system");
  document.title = "";
});

describe("App", () => {
  it("smoke: landmarks, providers and the welcome page when no process is open", async () => {
    const { api } = start();
    expect(screen.getByRole("banner")).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "Processes" })).toBeTruthy();
    expect(screen.getByRole("main")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "No process open" })).toBeTruthy();
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.getByTestId("job-watcher")).toBeTruthy();
    expect(screen.getByLabelText("Notifications")).toBeTruthy();
    expect(await screen.findByText("open · llm claude-code")).toBeTruthy();
    expect(api.meta).toHaveBeenCalledOnce();
    expect(document.title).toBe("Wynd");
  });

  it("opens the URL's process at load: a spinner, then the graph tab in the design surface", async () => {
    const { design, release } = start({ process: PID }, { hold: true });
    expect(design.open).toHaveBeenCalledWith(PID);
    expect(screen.getByRole("status", { name: `Loading ${PID}` })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Graph" }).getAttribute("aria-selected")).toBe("true");
    await release();
    const panel = screen.getByRole("tabpanel", { name: "Graph" });
    expect(panel.textContent).toBe(`graph of ${PID}inspector`);
    expect(screen.getByTestId("design-surface").contains(screen.getByText("inspector"))).toBe(true);
    expect(document.title).toBe(`${PID} · Wynd`);
  });

  it("URL state round-trips: ?process=…&tab=runs&run=… opens that run", async () => {
    const { design } = start({ process: PID, tab: "runs", run: "run_20260927T101500000_1c2d3e" });
    expect(screen.getByRole("tab", { name: "Runs" }).getAttribute("aria-selected")).toBe("true");
    expect(screen.getByRole("tabpanel", { name: "Runs" }).textContent).toBe(
      `runs of ${PID}, run run_20260927T101500000_1c2d3e`,
    );
    await waitFor(() => expect(design.open).toHaveBeenCalledWith(PID));
    expect(window.location.search).toBe(`?process=${PID}&tab=runs&run=run_20260927T101500000_1c2d3e`);
  });

  it("switching tabs replaces the URL and shows that tab's panel", async () => {
    start({ process: PID });
    await screen.findByText(`graph of ${PID}`);
    const length = window.history.length;
    fireEvent.click(screen.getByRole("tab", { name: "Releases" }));
    expect(screen.getByRole("tabpanel", { name: "Releases" }).textContent).toBe(`releases of ${PID}`);
    expect(window.location.search).toBe(`?process=${PID}&tab=releases`);
    expect(window.history.length).toBe(length);
    fireEvent.keyDown(screen.getByRole("tablist"), { key: "Home" });
    expect(screen.getByText(`graph of ${PID}`)).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "Process" }));
    expect(screen.getByText("process settings")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "YAML" }));
    expect(screen.getByText("yaml view")).toBeTruthy();
  });

  it("a process that no longer exists: toast, and the URL drops it", async () => {
    start({ process: "gone/away", tab: "runs", run: "r1" });
    expect(await screen.findByText("Process gone/away no longer exists")).toBeTruthy();
    expect(window.location.search).toBe("?tab=runs");
    expect(screen.getByRole("heading", { name: "No process open" })).toBeTruthy();
  });

  it("any other failure to open shows the error with Retry", async () => {
    const { design } = start({ process: PID }, {
      setup: (d) => vi.mocked(d.open).mockRejectedValueOnce(new ApiError(500, "internal", "loader crashed")),
    });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("internal");
    expect(alert.textContent).toContain("loader crashed");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(`graph of ${PID}`)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(design.open).toHaveBeenCalledTimes(2);
  });

  it("back/forward to another process commits the open one, then opens the other", async () => {
    const { design } = start({ process: PID });
    await screen.findByText(`graph of ${PID}`);
    act(() => {
      window.history.pushState(null, "", "/?process=finance%2Fmonthly_close");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByText("graph of finance/monthly_close")).toBeTruthy();
    expect(design.commit).toHaveBeenCalledWith("process_switch");
    expect(vi.mocked(design.commit).mock.invocationCallOrder[0]).toBeLessThan(
      vi.mocked(design.open).mock.invocationCallOrder[1] ?? 0,
    );
    act(() => {
      window.history.pushState(null, "", "/");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    await waitFor(() => expect(design.close).toHaveBeenCalledOnce());
    expect(screen.getByRole("heading", { name: "No process open" })).toBeTruthy();
  });

  it("back/forward while the open process cannot be committed stays on it", async () => {
    const { design } = start({ process: PID });
    await screen.findByText(`graph of ${PID}`);
    vi.mocked(design.commit).mockRejectedValueOnce(new ApiError(0, "network", "offline"));
    act(() => {
      window.history.pushState(null, "", "/?process=finance%2Fmonthly_close");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByText(`Could not commit ${PID}: offline`)).toBeTruthy();
    expect(window.location.search).toBe(`?process=${PID}`);
    expect(design.open).toHaveBeenCalledTimes(1);
    expect(screen.getByText(`graph of ${PID}`)).toBeTruthy();
  });

  it("with the controller unreachable the connection banner shows, then recovers", async () => {
    vi.useFakeTimers();
    const api = createFakeApi();
    vi.mocked(api.meta).mockRejectedValueOnce(new ApiError(0, "network", "Can't reach wynd serve-api"));
    vi.mocked(api.health).mockRejectedValueOnce(new ApiError(0, "network", "still down"));
    start({}, { api });
    await act(async () => {});
    expect(screen.getByRole("alert").textContent).toBe("Can't reach wynd serve-api. Retrying…");
    expect(screen.getByTestId("chat").textContent).toBe("open · llm not loaded");
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(api.health).toHaveBeenCalledOnce();
    expect(screen.getByRole("alert")).toBeTruthy();
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(api.health).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(api.meta).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("chat").textContent).toBe("open · llm claude-code");
  });

  it("the theme toggle cycles data-theme on the document", () => {
    start();
    const root = document.documentElement;
    const toggle = (): void => {
      fireEvent.click(screen.getByRole("button", { name: /^Theme:/ }));
    };
    toggle();
    expect(root.dataset.theme).toBe("light");
    toggle();
    expect(root.dataset.theme).toBe("dark");
    toggle();
    expect(root.hasAttribute("data-theme")).toBe(false);
  });

  it("header toggles hide and show the sidebar and the chat", () => {
    start();
    fireEvent.click(screen.getByRole("button", { name: "Hide processes" }));
    expect(screen.queryByRole("navigation", { name: "Processes" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show processes" }));
    expect(screen.getByRole("navigation", { name: "Processes" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Hide chat" }));
    expect(screen.getByTestId("chat").textContent).toMatch(/^closed/);
    expect(document.getElementById("wy-chat")?.hidden).toBe(true);
  });

  it("opening a chat through the URL opens the chat panel", () => {
    start();
    fireEvent.click(screen.getByRole("button", { name: "Hide chat" }));
    act(() => {
      window.history.pushState(null, "", "/?chat=chat_1");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByTestId("chat").textContent).toMatch(/^open/);
  });

  it("Ctrl/Cmd+K opens the sidebar and focuses the process search", async () => {
    start();
    fireEvent.click(screen.getByRole("button", { name: "Hide processes" }));
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    await waitFor(() => expect(document.activeElement?.id).toBe("wynd-process-search"));
    expect(screen.getByRole("navigation", { name: "Processes" })).toBeTruthy();
    (document.activeElement as HTMLElement).blur();
    fireEvent.keyDown(window, { key: "k" });
    fireEvent.keyDown(window, { key: "K", ctrlKey: true });
    await waitFor(() => expect(document.activeElement?.id).toBe("wynd-process-search"));
  });
});

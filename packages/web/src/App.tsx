// App shell: providers (Api, Meta, Design), layout, tabs, ConnectionBanner, JobWatcher, toasts (`$DRAFTS/07 §4`).
// The URL's process is opened here on load and on browser history navigation; in-app switches go through
// openProcess() (state/openProcess.ts), whose pushState fires no popstate.
import { useCallback, useEffect, useState, type ReactElement } from "react";
import { ApiError, createApi, type Api } from "./api/client";
import { ApiContext, DesignContext, MetaContext, useDesign } from "./api/context";
import { ChatPanel } from "./components/chat/ChatPanel";
import { ErrorBox } from "./components/common/ErrorBox";
import { Spinner } from "./components/common/Spinner";
import { ConflictBanner } from "./components/graph/ConflictBanner";
import { DesignSurface } from "./components/graph/DesignSurface";
import { GraphEditor } from "./components/graph/GraphEditor";
import { Inspector } from "./components/inspector/Inspector";
import { PROCESS_SEARCH_ID } from "./components/process/ProcessSearch";
import { ProcessSettings } from "./components/process-settings/ProcessSettings";
import { ReleasesPanel } from "./components/releases/ReleasesPanel";
import { RunPanel } from "./components/runs/RunPanel";
import { ConnectionBanner } from "./components/shell/ConnectionBanner";
import { CHAT_ID, Header } from "./components/shell/Header";
import { Sidebar } from "./components/shell/Sidebar";
import { Tabs, tabId, tabPanelId, type TabItem } from "./components/shell/Tabs";
import { Toasts } from "./components/shell/Toasts";
import { YamlView } from "./components/yaml/YamlView";
import { setHealthProbe } from "./state/connection";
import { createDesignSession, type DesignSession } from "./state/design";
import { JobWatcher } from "./state/jobs";
import { useQuery } from "./state/query";
import { useStore } from "./state/store";
import { toast } from "./state/toasts";
import { readUrl, useUrlState, type SetUrl, type Tab } from "./state/url";

export interface AppProps {
  api?: Api;                         // default createApi(); tests inject a fake
  design?: DesignSession;            // default createDesignSession(api); tests inject a fake
}

export const WIDE_QUERY = "(min-width: 1100px)";   // below it the sidebar and chat are overlay drawers

const TABS: TabItem<Tab>[] = [
  { id: "graph", label: "Graph" },
  { id: "process", label: "Process" },
  { id: "yaml", label: "YAML" },
  { id: "runs", label: "Runs" },
  { id: "releases", label: "Releases" },
];

export function App(props: AppProps): ReactElement {
  const [api] = useState(() => props.api ?? createApi());
  const [design] = useState(() => props.design ?? createDesignSession(api));
  const meta = useQuery("meta", () => api.meta());

  useEffect(() => setHealthProbe(() => api.health()), [api]);

  return (
    <ApiContext.Provider value={api}>
      <MetaContext.Provider value={meta.data ?? null}>
        <DesignContext.Provider value={design}>
          <Shell />
          <JobWatcher />
        </DesignContext.Provider>
      </MetaContext.Provider>
    </ApiContext.Provider>
  );
}

function isWide(): boolean {
  return typeof window.matchMedia === "function" ? window.matchMedia(WIDE_QUERY).matches : true;
}

function Shell(): ReactElement {
  const design = useDesign();
  const [url, setUrl] = useUrlState();
  const [sidebarOpen, setSidebarOpen] = useState(isWide);
  const [chatOpen, setChatOpen] = useState(isWide);
  const opening = useUrlProcess(design, url.process, setUrl);
  const openId = useStore(design.store, (s) => s?.processId ?? null);

  useEffect(() => {
    document.title = url.process === null ? "Wynd" : `${url.process} · Wynd`;
    if (!isWide()) setSidebarOpen(false);
  }, [url.process]);

  useEffect(() => {
    if (url.chat !== null) setChatOpen(true);
  }, [url.chat]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key.toLowerCase() !== "k" || !(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
      e.preventDefault();
      setSidebarOpen(true);
      // after the render that un-hides the sidebar
      setTimeout(() => document.getElementById(PROCESS_SEARCH_ID)?.focus(), 0);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  return (
    <div className="wy-app">
      <ConnectionBanner />
      <Header
        sidebarOpen={sidebarOpen}
        chatOpen={chatOpen}
        onToggleSidebar={() => setSidebarOpen((open) => !open)}
        onToggleChat={() => setChatOpen((open) => !open)}
      />
      <div className="wy-body">
        <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />
        <main className="wy-main">
          {url.process === null ? (
            <Welcome />
          ) : (
            <>
              <Tabs label="Process views" tabs={TABS} value={url.tab} onChange={(tab) => setUrl({ tab })} />
              <div role="tabpanel" id={tabPanelId(url.tab)} aria-labelledby={tabId(url.tab)} className="wy-tabpanel">
                <TabContent
                  pid={url.process}
                  tab={url.tab}
                  loaded={openId === url.process}
                  error={opening.error}
                  retry={opening.retry}
                />
              </div>
            </>
          )}
        </main>
        <div id={CHAT_ID} className="wy-aside" hidden={!chatOpen}>
          <ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} />
        </div>
      </div>
      <Toasts />
    </div>
  );
}

interface TabContentProps {
  pid: string;
  tab: Tab;
  loaded: boolean;                   // the design session holds `pid`
  error: Error | null;               // opening `pid` failed
  retry(): void;
}

function TabContent({ pid, tab, loaded, error, retry }: TabContentProps): ReactElement {
  switch (tab) {
    case "runs":
      return <RunPanel processId={pid} />;
    case "releases":
      return <ReleasesPanel processId={pid} />;
  }
  if (error !== null) return <ErrorBox error={error} onRetry={retry} />;
  if (!loaded) {
    return (
      <div className="wy-loading">
        <Spinner label={`Loading ${pid}`} />
      </div>
    );
  }
  switch (tab) {
    case "graph":
      return (
        <DesignSurface className="wy-design">
          <ConflictBanner />
          <div className="wy-graph-layout">
            <GraphEditor />
            <Inspector />
          </div>
        </DesignSurface>
      );
    case "process":
      return (
        <DesignSurface className="wy-design">
          <ConflictBanner />
          <ProcessSettings />
        </DesignSurface>
      );
    case "yaml":
      return <YamlView />;
  }
}

function Welcome(): ReactElement {
  return (
    <div className="wy-welcome">
      <h2>No process open</h2>
      <p>
        Pick a process from the list, or create one with <strong>+ New process</strong>. <kbd>Ctrl</kbd>+<kbd>K</kbd>{" "}
        searches processes.
      </p>
    </div>
  );
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Opens the URL's process at load and after back/forward; a 404 drops it from the URL with a toast. `error` is the
 *  failure to open `pid` (the URL's process). */
function useUrlProcess(design: DesignSession, pid: string | null, setUrl: SetUrl): { error: Error | null; retry(): void } {
  const [failed, setFailed] = useState<{ pid: string; error: Error } | null>(null);

  const open = useCallback(async (next: string): Promise<void> => {
    setFailed(null);
    try {
      await design.open(next);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        toast(`Process ${next} no longer exists`, { level: "warning" });
        design.close();
        setUrl({ process: null, sel: null, run: null }, "replace");
        return;
      }
      setFailed({ pid: next, error: err instanceof Error ? err : new Error(String(err)) });
    }
  }, [design, setUrl]);

  useEffect(() => {
    const initial = readUrl().process;
    if (initial !== null && design.store.get()?.processId !== initial) void open(initial);
  }, [design, open]);

  useEffect(() => {
    const onPopState = async (): Promise<void> => {
      const next = readUrl().process;
      const current = design.store.get()?.processId ?? null;
      if (next === current) return;
      if (current !== null) {
        try {
          await design.commit("process_switch");
        } catch (err) {
          // Keep the unsaved design: stay on the process that is still open.
          toast(`Could not commit ${current}: ${message(err)}`, { level: "error" });
          setUrl({ process: current }, "push");
          return;
        }
      }
      if (next === null) design.close();
      else await open(next);
    };
    const listener = (): void => void onPopState();
    window.addEventListener("popstate", listener);
    return () => window.removeEventListener("popstate", listener);
  }, [design, open, setUrl]);

  const retry = useCallback(() => {
    if (pid !== null) void open(pid);
  }, [open, pid]);

  return { error: failed !== null && failed.pid === pid ? failed.error : null, retry };
}

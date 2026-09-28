// App header: open process, status badges, HEAD, ProcessActions, save status, Settings (RegistriesDialog), theme
// (`$DRAFTS/07 §4.1`, §6.2, §6.3; PLAN §10 amendment 4). The open process's summary is the query
// `processes:<pid>`, so every `invalidate("processes")` (commits, integration, releases) refreshes it.
import { useState, type ReactElement } from "react";
import { useApi } from "../../api/context";
import { useQuery } from "../../state/query";
import { useUrlState } from "../../state/url";
import { Icon } from "../common/Icon";
import { ProcessActions } from "../process/ProcessActions";
import { StatusBadges } from "../process/StatusBadges";
import { RegistriesDialog } from "../registries/RegistriesDialog";
import { SIDEBAR_ID } from "./Sidebar";
import { StatusBar } from "./StatusBar";
import { ThemeToggle } from "./ThemeToggle";

export interface HeaderProps {
  sidebarOpen: boolean;
  chatOpen: boolean;
  onToggleSidebar(): void;
  onToggleChat(): void;
}

export const CHAT_ID = "wy-chat";

export function Header({ sidebarOpen, chatOpen, onToggleSidebar, onToggleChat }: HeaderProps): ReactElement {
  const api = useApi();
  const [url] = useUrlState();
  const pid = url.process;
  const summary = useQuery(`processes:${pid ?? ""}`, () => api.processes.get(pid ?? ""), { enabled: pid !== null });
  const status = pid === null ? null : summary.data?.status ?? null;
  const [settingsOpen, setSettingsOpen] = useState(false);

  return (
    <header className="wy-header">
      <div className="wy-header-start">
        <button
          type="button"
          className="wy-icon-btn"
          aria-label={sidebarOpen ? "Hide processes" : "Show processes"}
          aria-expanded={sidebarOpen}
          aria-controls={SIDEBAR_ID}
          onClick={onToggleSidebar}
        >
          <Icon name="menu" />
        </button>
        <h1 className="wy-title">
          <span className="wy-brand">Wynd</span>
          {pid !== null && (
            <>
              <span className="wy-crumb" aria-hidden="true">▸</span>
              <span className="wy-process-id">{pid}</span>
            </>
          )}
        </h1>
        {pid !== null && <StatusBadges status={status} />}
        {status?.head != null && (
          <span className="wy-head" title={`${status.head.subject} (${status.head.at})`}>
            HEAD <code>{status.head.short}</code>
          </span>
        )}
      </div>
      <div className="wy-header-end">
        {pid !== null && <ProcessActions processId={pid} />}
        <StatusBar />
        <button type="button" onClick={() => setSettingsOpen(true)}>
          <Icon name="settings" />
          Settings
        </button>
        <ThemeToggle />
        <button
          type="button"
          className="wy-icon-btn"
          aria-label={chatOpen ? "Hide chat" : "Show chat"}
          aria-expanded={chatOpen}
          aria-controls={CHAT_ID}
          onClick={onToggleChat}
        >
          <Icon name="chat" />
        </button>
      </div>
      <RegistriesDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </header>
  );
}

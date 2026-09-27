// App header: open process, status badges, HEAD, ProcessActions, save status, Settings (RegistriesDialog), theme
// (`$DRAFTS/07 §4.1`, §6.3; PLAN §10 amendment 4). Stub from WEB-SCAFFOLD; WEB-CORE implements it.
import type { ReactElement } from "react";

export interface HeaderProps {
  sidebarOpen: boolean;
  chatOpen: boolean;
  onToggleSidebar(): void;
  onToggleChat(): void;
}

export function Header(_props: HeaderProps): ReactElement | null {
  return null;
}

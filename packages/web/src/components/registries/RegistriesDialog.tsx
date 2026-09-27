// Settings: MCP servers (add url + auth env, OAuth start -> window.location.assign(authorize_url), remove) and
// providers (PLAN §10 amendment 4). Opened from the header "Settings" button. Stub from WEB-SCAFFOLD; WEB-OPS implements it.
import type { ReactElement } from "react";

export interface RegistriesDialogProps {
  open: boolean;
  onClose(): void;
}

export function RegistriesDialog(_props: RegistriesDialogProps): ReactElement | null {
  return null;
}

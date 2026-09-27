// <aside aria-label="Chat">: chat list + the open chat (URL `chat`); a drawer below 1100px (`$DRAFTS/07 §9.1`).
// Stub from WEB-SCAFFOLD; WEB-CHAT implements it.
import type { ReactElement } from "react";

export interface ChatPanelProps {
  open: boolean;
  onClose(): void;
}

export function ChatPanel(_props: ChatPanelProps): ReactElement | null {
  return null;
}

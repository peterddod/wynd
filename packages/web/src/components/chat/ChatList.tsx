// Chats by updated_at desc, job badges, [+ New] (`$DRAFTS/07 §9.1`). Stub from WEB-SCAFFOLD; WEB-CHAT implements it.
import type { ReactElement } from "react";

export interface ChatListProps {
  selected: string | null;
  onSelect(chatId: string): void;
}

export function ChatList(_props: ChatListProps): ReactElement | null {
  return null;
}

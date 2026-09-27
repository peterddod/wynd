// User bubble (with its acting-on chip) or assistant Markdown (aria-busy while streaming) (`$DRAFTS/07 §9.3`).
// Stub from WEB-SCAFFOLD; WEB-CHAT implements it.
import type { ReactElement } from "react";
import type { AssistantItem, UserItem } from "../../api/types";

export interface MessageItemProps {
  item: UserItem | AssistantItem;
  onRetry?(): void;                  // assistant error: resend the previous user text
}

export function MessageItem(_props: MessageItemProps): ReactElement | null {
  return null;
}

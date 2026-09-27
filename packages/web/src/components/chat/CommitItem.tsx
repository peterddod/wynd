// "Committed <short> <message first line>" (`$DRAFTS/07 §9.3`). Stub from WEB-SCAFFOLD; WEB-CHAT implements it.
import type { ReactElement } from "react";
import type { CommitItem as CommitChatItem } from "../../api/types";

export interface CommitItemProps {
  item: CommitChatItem;
}

export function CommitItem(_props: CommitItemProps): ReactElement | null {
  return null;
}

// "Committed <short> <message first line>" (`$DRAFTS/07 §9.3`). The design reload happens on the turn event.
import type { ReactElement } from "react";
import type { CommitItem as CommitChatItem } from "../../api/types";

export interface CommitItemProps {
  item: CommitChatItem;
}

export function CommitItem({ item }: CommitItemProps): ReactElement {
  const subject = item.message.split("\n", 1)[0] ?? "";
  return (
    <p className="wc-item wc-commit">
      Committed <code title={item.sha}>{item.sha.slice(0, 7)}</code> {subject}
    </p>
  );
}

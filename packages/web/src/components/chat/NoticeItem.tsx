// Notice coloured by level, with the level also in text (`$DRAFTS/07 §9.3`, §4.7: colour is never the only signal).
import type { ReactElement } from "react";
import type { NoticeItem as NoticeChatItem } from "../../api/types";

export interface NoticeItemProps {
  item: NoticeChatItem;
}

const LEVEL_LABEL: Record<NoticeChatItem["level"], string> = { info: "Note", warning: "Warning", error: "Error" };

export function NoticeItem({ item }: NoticeItemProps): ReactElement {
  return (
    <p className="wc-item wc-notice" data-level={item.level}>
      <strong className="wc-notice-level">{LEVEL_LABEL[item.level]}:</strong> {item.text}
    </p>
  );
}

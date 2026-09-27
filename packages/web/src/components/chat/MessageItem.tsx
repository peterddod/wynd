// User bubble (with its acting-on chip) or assistant Markdown (aria-busy while streaming) (`$DRAFTS/07 §9.3`).
import type { ReactElement } from "react";
import type { AssistantItem, UserItem } from "../../api/types";
import { Markdown } from "./Markdown";

export interface MessageItemProps {
  item: UserItem | AssistantItem;
  onRetry?(): void;                  // assistant error: resend the previous user text
}

export function MessageItem({ item, onRetry }: MessageItemProps): ReactElement {
  if (item.type === "user") {
    return (
      <div className="wc-item wc-user">
        <p className="wc-bubble">{item.text}</p>
        <span className="wc-chip" data-none={item.acting_on === null ? "" : undefined}>
          {item.acting_on === null ? "no process" : `acting on: ${item.acting_on}`}
        </span>
      </div>
    );
  }
  const streaming = item.status === "streaming";
  return (
    <div className="wc-item wc-assistant" aria-live="polite" aria-busy={streaming} data-status={item.status}>
      <Markdown text={item.text} />
      {streaming && item.text === "" && <p className="wc-muted wc-thinking">Thinking…</p>}
      {item.status === "error" && (
        <p className="wc-error" role="alert">
          {item.error ?? "The assistant failed."}{" "}
          {onRetry !== undefined && <button type="button" onClick={onRetry}>Retry</button>}
        </p>
      )}
      {item.status === "cancelled" && <p className="wc-muted">Stopped.</p>}
    </div>
  );
}

// Inline error with code, message and [Retry] (`$DRAFTS/07 §4.6`).
import type { ReactElement } from "react";
import { ApiError } from "../../api/client";
import { Icon } from "./Icon";

export interface ErrorBoxProps {
  error: Error | null;               // an ApiError shows its code
  onRetry?(): void;
}

export function ErrorBox({ error, onRetry }: ErrorBoxProps): ReactElement | null {
  if (error === null) return null;
  return (
    <div className="wy-error" role="alert">
      <Icon name="warning" />
      {error instanceof ApiError && <code className="wy-error-code">{error.code}</code>}
      <span className="wy-error-message">{error.message}</span>
      {onRetry !== undefined && (
        <button type="button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

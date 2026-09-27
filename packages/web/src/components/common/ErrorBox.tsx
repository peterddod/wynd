// Inline error with code, message and [Retry] (`$DRAFTS/07 §4.6`). Stub from WEB-SCAFFOLD; WEB-CORE implements it.
import type { ReactElement } from "react";

export interface ErrorBoxProps {
  error: Error | null;               // an ApiError shows its code
  onRetry?(): void;
}

export function ErrorBox(_props: ErrorBoxProps): ReactElement | null {
  return null;
}

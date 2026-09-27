// ActingOnChip + textarea + [Send]/[Stop]; Enter sends (`$DRAFTS/07 §9.4`). Stub from WEB-SCAFFOLD; WEB-CHAT implements it.
import type { ReactElement } from "react";

export interface ComposerProps {
  chatId: string;
  running: boolean;                  // a turn is running: disabled, [Stop] shown
}

export function Composer(_props: ComposerProps): ReactElement | null {
  return null;
}

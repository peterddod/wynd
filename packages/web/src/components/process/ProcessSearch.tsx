// Search input (250 ms debounce) + the four ANDed flag chips (`$DRAFTS/07 §6.1`). Stub from WEB-SCAFFOLD; WEB-OPS implements it.
import type { ReactElement } from "react";
import type { StatusFlag } from "../../api/types";

/** id of the search input: Ctrl/Cmd+K and the chat's acting-on chip focus it. */
export const PROCESS_SEARCH_ID = "wynd-process-search";

export interface ProcessSearchProps {
  q: string;
  flags: StatusFlag[];
  onChange(q: string, flags: StatusFlag[]): void;
}

export function ProcessSearch(_props: ProcessSearchProps): ReactElement | null {
  return null;
}

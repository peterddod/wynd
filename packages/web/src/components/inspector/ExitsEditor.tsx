// Exits with per-exit field tables: add, rename, remove (`$DRAFTS/07 §7.8`, §7.9). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";
import type { Json } from "../../api/types";

export interface ExitsEditorProps {
  exits: string[];
  fields(exit: string): [string, Json][];
  onFieldsChange(exit: string, rows: [string, Json][]): void;
  onFieldRename(exit: string, from: string, to: string): void;
  onAdd(name: string): void;
  onRename(from: string, to: string): void;
  onRemove(name: string): void;
  typeOptions: string[];
  readOnly?: boolean;
}

export function ExitsEditor(_props: ExitsEditorProps): ReactElement | null {
  return null;
}

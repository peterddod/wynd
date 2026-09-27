// Expression lexer, step rename inside expressions, completion prefix (`$DRAFTS/07 §7.3`).
// Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.

export type Tok = {
  t: "ws" | "str" | "num" | "id" | "punct";
  s: string;
  start: number;
  end: number;
  quote?: '"' | "'";
};

/** null on an unterminated string */
export function lex(expr: string): Tok[] | null {
  throw new Error("not implemented");
}

/** null if lexing fails; byte-identical output when nothing matches */
export function renameStepInExpr(expr: string, from: string, to: string): string | null {
  throw new Error("not implemented");
}

export function completionPrefix(expr: string, caret: number): { start: number; prefix: string } | null {
  throw new Error("not implemented");
}

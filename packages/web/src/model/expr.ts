// Expression lexer, step rename inside expressions, completion prefix (`$DRAFTS/07 §7.3`). The lexer only needs to be
// good enough for renaming and completion; the controller is the authority on the grammar.

export type Tok = {
  t: "ws" | "str" | "num" | "id" | "punct";
  s: string;
  start: number;
  end: number;
  quote?: '"' | "'";
};

const WS = /\s+/y;
const NUM = /\d+(\.\d+)?/y;
const ID = /[A-Za-z_][A-Za-z0-9_]*/y;

/** null on an unterminated string */
export function lex(expr: string): Tok[] | null {
  const toks: Tok[] = [];
  let i = 0;
  while (i < expr.length) {
    const ch = expr[i] as string;
    if (ch === '"' || ch === "'") {
      let j = i + 1;
      while (j < expr.length && expr[j] !== ch) j += expr[j] === "\\" ? 2 : 1;
      if (j >= expr.length) return null;
      toks.push({ t: "str", s: expr.slice(i, j + 1), start: i, end: j + 1, quote: ch });
      i = j + 1;
      continue;
    }
    const matched = match(WS, "ws", expr, i) ?? match(NUM, "num", expr, i) ?? match(ID, "id", expr, i);
    const tok = matched ?? { t: "punct", s: ch, start: i, end: i + 1 };
    toks.push(tok);
    i = tok.end;
  }
  return toks;
}

function match(re: RegExp, t: Tok["t"], expr: string, at: number): Tok | null {
  re.lastIndex = at;
  const m = re.exec(expr);
  return m === null ? null : { t, s: m[0], start: at, end: at + m[0].length };
}

/** The n-th non-whitespace token before index i (n = 1 is the nearest). */
function before(toks: Tok[], i: number, n: number): Tok | undefined {
  let seen = 0;
  for (let j = i - 1; j >= 0; j--) {
    const tok = toks[j] as Tok;
    if (tok.t === "ws") continue;
    seen += 1;
    if (seen === n) return tok;
  }
  return undefined;
}

function is(tok: Tok | undefined, t: Tok["t"], s: string): boolean {
  return tok !== undefined && tok.t === t && tok.s === s;
}

/** Rewrites `steps.<from>` references and `edges["<from>.<exit>"]` keys; null if lexing fails; byte-identical output
 *  when nothing matches. A reference is only rewritten when it is not itself a member (`x.steps.a` stays). */
export function renameStepInExpr(expr: string, from: string, to: string): string | null {
  const toks = lex(expr);
  if (toks === null) return null;
  const parts = toks.map((tok, i) => {
    const notMember = !is(before(toks, i, 3), "punct", ".");
    if (tok.t === "id" && tok.s === from && is(before(toks, i, 1), "punct", ".") && is(before(toks, i, 2), "id", "steps")
        && notMember) {
      return to;
    }
    if (tok.t === "str" && is(before(toks, i, 1), "punct", "[") && is(before(toks, i, 2), "id", "edges") && notMember) {
      const body = tok.s.slice(1, -1);
      if (body.startsWith(`${from}.`)) return `${tok.quote}${to}${body.slice(from.length)}${tok.quote}`;
    }
    return tok.s;
  });
  return parts.join("");
}

const PREFIX = /[A-Za-z_]\w*(?:\.[A-Za-z_]\w*|\["[^"]*"\]|\[\d+\])*\.?$/;

/** The reference being typed before the caret; null inside a string literal or when there is none. */
export function completionPrefix(expr: string, caret: number): { start: number; prefix: string } | null {
  const head = expr.slice(0, caret);
  if (lex(head) === null) return null;
  const m = PREFIX.exec(head);
  if (m === null) return null;
  return { start: m.index, prefix: m[0] };
}

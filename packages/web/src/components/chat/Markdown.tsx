// Safe Markdown subset to React elements, no HTML passthrough (`$DRAFTS/07 §9.10`). Blocks: fenced code (an unclosed
// fence renders as code while streaming), `#`/`##`/`###` headings, one-level `-`/`*`/`1.` lists, `>` quotes,
// paragraphs (single newlines are line breaks). Inline: `code`, **bold**, *em*, [text](http(s)://…) links; a link
// with any other scheme stays literal text. Headings render as h3-h5: the chat sits under the page's own outline.
import { Fragment, type ReactElement, type ReactNode } from "react";

export interface MarkdownProps {
  text: string;
}

type Block =
  | { type: "code"; lang: string; text: string }
  | { type: "heading"; level: 1 | 2 | 3; text: string }
  | { type: "quote"; lines: string[] }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "paragraph"; lines: string[] };

const FENCE = /^\s*(`{3,}|~{3,})\s*([\w+-]*)\s*$/;
const HEADING = /^(#{1,3})\s+(.*?)\s*#*\s*$/;
const QUOTE = /^>\s?(.*)$/;
const BULLET = /^\s*[-*]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

function startsBlock(line: string): boolean {
  return FENCE.test(line) || HEADING.test(line) || QUOTE.test(line) || BULLET.test(line) || NUMBERED.test(line);
}

function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";
    const fence = FENCE.exec(line);
    if (fence !== null) {
      const marker = fence[1] ?? "```";
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !(lines[i] ?? "").trim().startsWith(marker)) body.push(lines[i++] ?? "");
      i += 1;                                                  // the closing fence (or past the end)
      blocks.push({ type: "code", lang: fence[2] ?? "", text: body.join("\n") });
      continue;
    }
    if (line.trim() === "") {
      i += 1;
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading !== null) {
      blocks.push({ type: "heading", level: (heading[1] ?? "#").length as 1 | 2 | 3, text: heading[2] ?? "" });
      i += 1;
      continue;
    }
    if (QUOTE.test(line)) {
      const quoted: string[] = [];
      for (let m = QUOTE.exec(lines[i] ?? ""); m !== null; m = QUOTE.exec(lines[++i] ?? "")) quoted.push(m[1] ?? "");
      blocks.push({ type: "quote", lines: quoted });
      continue;
    }
    const ordered = NUMBERED.test(line);
    if (ordered || BULLET.test(line)) {
      const pattern = ordered ? NUMBERED : BULLET;
      const items: string[] = [];
      for (let m = pattern.exec(lines[i] ?? ""); m !== null; m = pattern.exec(lines[++i] ?? "")) items.push(m[1] ?? "");
      blocks.push({ type: "list", ordered, items });
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && (lines[i] ?? "").trim() !== "" && (para.length === 0 || !startsBlock(lines[i] ?? ""))) {
      para.push(lines[i++] ?? "");
    }
    blocks.push({ type: "paragraph", lines: para });
  }
  return blocks;
}

// One inline token per alternative: code span, bold, em, link.
const INLINE = /`([^`\n]+)`|\*\*(.+?)\*\*|\*([^*\s](?:[^*]*[^*\s])?)\*|\[([^\]\n]+)\]\(([^)\s]+)\)/g;
const SAFE_URL = /^https?:\/\//i;

function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(INLINE)) {
    const at = m.index;
    if (at > last) out.push(text.slice(last, at));
    const key = out.length;
    const [whole, code, bold, em, label, href] = m;
    if (code !== undefined) out.push(<code key={key}>{code}</code>);
    else if (bold !== undefined) out.push(<strong key={key}>{inline(bold)}</strong>);
    else if (em !== undefined) out.push(<em key={key}>{inline(em)}</em>);
    else if (label !== undefined && href !== undefined && SAFE_URL.test(href)) {
      out.push(<a key={key} href={href} target="_blank" rel="noopener noreferrer">{inline(label)}</a>);
    } else out.push(whole);
    last = at + whole.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function withBreaks(lines: string[]): ReactNode[] {
  return lines.map((line, i) => <Fragment key={i}>{i > 0 && <br />}{inline(line)}</Fragment>);
}

function renderBlock(block: Block, key: number): ReactElement {
  switch (block.type) {
    case "code":
      return (
        <pre key={key} className="wc-md-code" data-lang={block.lang || undefined}>
          <code>{block.text}</code>
        </pre>
      );
    case "heading": {
      const Tag = (["h3", "h4", "h5"] as const)[block.level - 1] ?? "h5";
      return <Tag key={key} className="wc-md-heading">{inline(block.text)}</Tag>;
    }
    case "quote":
      return <blockquote key={key}><p>{withBreaks(block.lines)}</p></blockquote>;
    case "list": {
      const items = block.items.map((item, i) => <li key={i}>{inline(item)}</li>);
      return block.ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>;
    }
    case "paragraph":
      return <p key={key}>{withBreaks(block.lines)}</p>;
  }
}

export function Markdown({ text }: MarkdownProps): ReactElement | null {
  const blocks = parseBlocks(text);
  if (blocks.length === 0) return null;
  return <div className="wc-md">{blocks.map(renderBlock)}</div>;
}

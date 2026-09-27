// The chat's safe Markdown subset (`$DRAFTS/07 §9.10`).
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";

function md(text: string): HTMLElement {
  const { container } = render(<Markdown text={text} />);
  return container;
}

describe("Markdown blocks", () => {
  it("renders headings one level under the page outline", () => {
    md("# Plan\n## Steps\n### Detail\n#### not a heading");
    expect(screen.getByRole("heading", { level: 3, name: "Plan" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 4, name: "Steps" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 5, name: "Detail" })).toBeTruthy();
    expect(screen.getByText("#### not a heading").tagName).toBe("P");
  });

  it("renders bullet and numbered lists (one level) and quotes", () => {
    const root = md("- read\n* extract\n\n1. validate\n2. save\n\n> kept as is\n> second line");
    const [bullets, numbered] = [root.querySelector("ul"), root.querySelector("ol")];
    expect([...(bullets?.querySelectorAll("li") ?? [])].map((li) => li.textContent)).toEqual(["read", "extract"]);
    expect([...(numbered?.querySelectorAll("li") ?? [])].map((li) => li.textContent)).toEqual(["validate", "save"]);
    const quote = root.querySelector("blockquote");
    expect(quote?.textContent).toBe("kept as issecond line");
    expect(quote?.querySelectorAll("br")).toHaveLength(1);
  });

  it("renders fenced code verbatim, and an unclosed fence as code while streaming", () => {
    const closed = md("Run this:\n```yaml\nsteps:\n  read: {use: ./steps/read_pdf}\n```\nDone.");
    const pre = closed.querySelector("pre");
    expect(pre?.dataset.lang).toBe("yaml");
    expect(pre?.textContent).toBe("steps:\n  read: {use: ./steps/read_pdf}");
    expect(screen.getByText("Done.").tagName).toBe("P");

    const open = md("```\nedges:\n  - from: read.done");
    expect(open.querySelector("pre code")?.textContent).toBe("edges:\n  - from: read.done");
  });

  it("keeps single newlines as line breaks and splits paragraphs on blank lines", () => {
    const root = md("line one\nline two\n\nsecond paragraph");
    const paragraphs = root.querySelectorAll("p");
    expect(paragraphs).toHaveLength(2);
    expect(paragraphs[0]?.querySelectorAll("br")).toHaveLength(1);
    expect(paragraphs[1]?.textContent).toBe("second paragraph");
  });

  it("renders nothing for empty text", () => {
    expect(md("").childElementCount).toBe(0);
    expect(md("\n\n").childElementCount).toBe(0);
  });
});

describe("Markdown inline", () => {
  it("renders code, bold, emphasis and nesting", () => {
    const root = md("Route `escalate.done` **through `notify_finance`** and *only* when needed");
    expect(root.querySelector("p > code")?.textContent).toBe("escalate.done");
    const bold = root.querySelector("strong");
    expect(bold?.textContent).toBe("through notify_finance");
    expect(bold?.querySelector("code")?.textContent).toBe("notify_finance");
    expect(root.querySelector("em")?.textContent).toBe("only");
    expect(root.querySelector("p")?.textContent).toBe("Route escalate.done through notify_finance and only when needed");
  });

  it("does not treat spaced asterisks as emphasis", () => {
    const root = md("total * 2 * 3");
    expect(root.querySelector("em")).toBeNull();
    expect(root.textContent).toBe("total * 2 * 3");
  });

  it("links http(s) targets in a new tab without an opener", () => {
    md("See [the docs](https://example.com/wynd) or [local](http://127.0.0.1:8780/).");
    const link = screen.getByRole("link", { name: "the docs" });
    expect(link.getAttribute("href")).toBe("https://example.com/wynd");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(screen.getByRole("link", { name: "local" }).getAttribute("href")).toBe("http://127.0.0.1:8780/");
  });

  it("keeps links with other schemes as plain text", () => {
    const root = md("[click](javascript:alert(1)) and [file](file:///etc/passwd)");
    expect(root.querySelector("a")).toBeNull();
    expect(root.textContent).toContain("[click](javascript:alert(1)");
    expect(root.textContent).toContain("[file](file:///etc/passwd)");
  });

  it("never passes HTML through", () => {
    const root = md('<img src="x" onerror="alert(1)"> <script>alert(2)</script> **<b>bold</b>**');
    expect(root.querySelector("img, script, b")).toBeNull();
    expect(root.textContent).toContain('<img src="x" onerror="alert(1)">');
    expect(root.querySelector("strong")?.textContent).toBe("<b>bold</b>");
  });
});

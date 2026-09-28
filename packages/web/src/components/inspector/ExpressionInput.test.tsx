import { act, fireEvent, screen } from "@testing-library/react";
import { useState, type ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ExprCheck, ExprCheckRequest, Loc } from "../../api/types";
import { createDesignSession, type DesignSession } from "../../state/design";
import { createFakeApi, type FakeApi } from "../../test/fakeApi";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { resetDoubles } from "../graph/testDoubles";
import { ExpressionInput, VALIDATE_MS } from "./ExpressionInput";

vi.mock("../../state/store", () => import("../graph/testDoubles"));
vi.mock("../../state/query", () => import("../graph/testDoubles"));
vi.mock("../../state/connection", () => import("../graph/testDoubles"));

const PID = "process_supplier_invoice";
const DEST: Loc = ["edges", 3, "to", 0, "with", "dest"];
const WHEN: Loc = ["edges", 3, "to", 1, "when"];

function Harness({ initial, loc }: { initial: string; loc: Loc }): ReactElement {
  const [value, setValue] = useState(initial);
  return <ExpressionInput id="expr" label="Condition" value={value} loc={loc} onChange={setValue} />;
}

async function setup(initial: string, loc: Loc = DEST, api: FakeApi = createFakeApi()): Promise<{ api: FakeApi; design: DesignSession }> {
  const design = createDesignSession(api);
  await design.open(PID);
  renderApp(<Harness initial={initial} loc={loc} />, { api, design, url: { process: PID } });
  return { api, design };
}

function requests(api: FakeApi): ExprCheckRequest[] {
  return api.calls.filter((c) => c.method === "expressions.validate").map((c) => c.args[0] as ExprCheckRequest);
}

function input(): HTMLTextAreaElement {
  return screen.getByLabelText("Condition") as HTMLTextAreaElement;
}

function type(text: string, caret = text.length): void {
  const el = input();
  fireEvent.change(el, { target: { value: text, selectionStart: caret, selectionEnd: caret } });
}

async function tick(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  resetDoubles();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("ExpressionInput", () => {
  it("validates 300 ms after the last change, with the in-flight doc and scope on the first request", async () => {
    const { api, design } = await setup("");
    type("steps.validate.outputs.fields.tot");
    await tick(VALIDATE_MS - 1);
    type("steps.validate.outputs.fields.totl");
    await tick(VALIDATE_MS - 1);
    expect(requests(api)).toHaveLength(0);
    await tick(1);
    expect(requests(api)).toEqual([{
      process_id: PID, process: design.processDoc(), protos: {}, loc: DEST,
      expr: "steps.validate.outputs.fields.totl", scope: true,
    }]);
    type("steps.validate.outputs.fields.total");
    await tick(VALIDATE_MS);
    expect(requests(api)[1]!.scope).toBe(false);
  });

  it("renders an error with aria-invalid and its column", async () => {
    await setup("");
    type("if steps.validate.outputs.fields.totl > 10000 then env.REVIEW_DIR else env.RECORDS_DIR");
    await tick(VALIDATE_MS);
    expect(input().getAttribute("aria-invalid")).toBe("true");
    const message = document.getElementById(input().getAttribute("aria-describedby")!)!;
    expect(message.textContent).toBe(`col 4: ${fixture("exprCheck").errors[0]!.message}`);
  });

  it("ignores an out-of-order response", async () => {
    const api = createFakeApi();
    const resolvers: ((r: ExprCheck) => void)[] = [];
    vi.mocked(api.expressions.validate).mockImplementation(() => new Promise((resolve) => resolvers.push(resolve)));
    await setup("", DEST, api);
    type("steps.a");
    await tick(VALIDATE_MS);
    type("steps.read.outputs.text");
    await tick(VALIDATE_MS);
    expect(resolvers).toHaveLength(2);
    await act(async () => resolvers[1]!({ ok: true, errors: [], warnings: [], scope: [] }));
    await act(async () => resolvers[0]!({ ok: false, errors: [{ message: "stale", start: 0, end: 5 }], warnings: [], scope: [] }));
    expect(input().getAttribute("aria-invalid")).toBe("false");
    expect(screen.queryByText(/stale/)).toBeNull();
  });

  it("asks for a condition without a request when a when is empty", async () => {
    const { api } = await setup("", WHEN);
    await tick(VALIDATE_MS * 2);
    expect(requests(api)).toHaveLength(0);
    expect(screen.getByText("Condition required, or tick 'Otherwise (else)'")).toBeTruthy();
    expect(input().getAttribute("aria-invalid")).toBe("true");
  });

  it("suggests scope refs by prefix and inserts one with Enter or Tab", async () => {
    await setup("");
    type("steps.validate.outputs.f");
    await tick(VALIDATE_MS);
    const list = screen.getByRole("listbox");
    const options = [...list.querySelectorAll("[role=option]")].map((o) => o.textContent);
    expect(options).toEqual(["steps.validate.outputs.fixable", "steps.validate.outputs.fields"]);
    expect(input().getAttribute("aria-activedescendant")).toBe(list.querySelector("[role=option]")!.id);
    fireEvent.keyDown(input(), { key: "ArrowDown" });
    fireEvent.keyDown(input(), { key: "Enter" });
    expect(input().value).toBe("steps.validate.outputs.fields");
    expect(screen.queryByRole("listbox")).toBeNull();
    type("steps.validate.outputs.fields.total > 1 and steps.fix.r");
    expect(screen.getByRole("listbox").textContent).toBe("steps.fix.runs");
    fireEvent.keyDown(input(), { key: "Tab" });
    expect(input().value).toBe("steps.validate.outputs.fields.total > 1 and steps.fix.runs");
  });

  it("closes suggestions on Escape and hides them inside a string literal", async () => {
    await setup("");
    type("steps.re");
    await tick(VALIDATE_MS);
    expect(screen.getByRole("listbox")).toBeTruthy();
    fireEvent.keyDown(input(), { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
    type('steps.read.exit == "steps.re');
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("appends the builtin function names to the candidates", async () => {
    await setup("");
    type("steps.read.outputs.text");
    await tick(VALIDATE_MS);
    type("le");
    expect(screen.getByRole("listbox").textContent).toBe("len");
  });
});

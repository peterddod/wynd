import { act, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { updateBranch } from "../../model/processDoc";
import { createDesignSession } from "../../state/design";
import { createFakeApi } from "../../test/fakeApi";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { resetDoubles } from "../graph/testDoubles";
import { YamlView } from "./YamlView";

vi.mock("../../state/store", () => import("../graph/testDoubles"));
vi.mock("../../state/query", () => import("../graph/testDoubles"));
vi.mock("../../state/connection", () => import("../graph/testDoubles"));

beforeEach(() => {
  resetDoubles();
});

describe("YamlView", () => {
  it("shows the saved YAML of the process and every proto, noting unsaved edits", async () => {
    const api = createFakeApi();
    const design = createDesignSession(api);
    await design.open("process_supplier_invoice");
    renderApp(<YamlView />, { api, design });
    const d = fixture("design");
    expect(screen.getByRole("region", { name: d.process_file.path }).querySelector("pre")!.textContent).toBe(d.process_file.yaml);
    expect(screen.getAllByRole("region")).toHaveLength(1 + Object.keys(d.protos).length);
    expect(screen.queryByText("Showing last saved version")).toBeNull();
    act(() => design.apply("edit", (x) => ({ ...x, process: updateBranch(x.process, 3, 1, { when: "x" }) })));
    expect(screen.getByText("Showing last saved version")).toBeTruthy();
  });
});

import { act, fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createFakeApi } from "../../test/fakeApi";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { invalidated, resetDoubles } from "../runs/testDoubles";
import { RegistriesDialog, browser } from "./RegistriesDialog";

vi.mock("../../state/query", () => import("../runs/testDoubles").then((m) => m.queryModule));
vi.mock("../common/Dialog", () => import("../runs/testDoubles").then((m) => m.dialogModule));
vi.mock("../common/ErrorBox", () => import("../runs/testDoubles").then((m) => m.errorBoxModule));

beforeEach(() => resetDoubles());

async function flush(): Promise<void> {
  await act(async () => {});
}

function server(name: string): HTMLElement {
  return screen.getByRole("listitem", { name: `MCP server ${name}` });
}

describe("RegistriesDialog", () => {
  it("closed: renders nothing and asks for nothing", () => {
    const { api } = renderApp(<RegistriesDialog open={false} onClose={() => {}} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(api.calls).toEqual([]);
  });

  it("lists MCP servers and providers", async () => {
    renderApp(<RegistriesDialog open onClose={() => {}} />);
    await screen.findByRole("listitem", { name: "MCP server github" });
    expect(within(server("github")).getByText("https://api.githubcopilot.com/mcp/")).toBeTruthy();
    expect(within(server("github")).getByText("auth env: GITHUB_TOKEN")).toBeTruthy();
    expect(within(server("files")).getByText("uvx mcp-server-filesystem /data/inbox")).toBeTruthy();
    expect(within(server("files")).queryByRole("button", { name: /OAuth/ })).toBeNull();
    await flush();
    const providers = screen.getByRole("region", { name: "Providers" });
    expect(within(providers).getByText("anthropic")).toBeTruthy();
    expect(within(providers).getByText("ANTHROPIC_API_KEY is not set")).toBeTruthy();
    expect(within(providers).getAllByText("not ready")).toHaveLength(1);
    expect(within(providers).getByText("cheap → haiku · standard → sonnet · strong → opus")).toBeTruthy();
  });

  it("adds an http server whose token is an env reference", async () => {
    const { api } = renderApp(<RegistriesDialog open onClose={() => {}} />);
    await flush();
    const add = screen.getByRole("button", { name: "Add server" }) as HTMLButtonElement;
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Linear!" } });
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "https://mcp.linear.app/mcp" } });
    expect(add.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "linear" } });
    fireEvent.change(screen.getByLabelText("Auth env var"), { target: { value: "LINEAR_TOKEN" } });
    expect(add.disabled).toBe(false);
    fireEvent.click(add);
    await flush();
    expect(api.registries.mcp.add).toHaveBeenCalledWith({
      name: "linear",
      transport: "http",
      url: "https://mcp.linear.app/mcp",
      headers: { Authorization: "Bearer ${env:LINEAR_TOKEN}" },
      auth_env: ["LINEAR_TOKEN"],
    });
    expect(invalidated).toContain("registries:mcp");
    expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("");
  });

  it("a server added without an auth env sends no headers", async () => {
    const { api } = renderApp(<RegistriesDialog open onClose={() => {}} />);
    await flush();
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "notion" } });
    fireEvent.change(screen.getByLabelText("URL"), { target: { value: "https://mcp.notion.com/mcp" } });
    fireEvent.click(screen.getByRole("button", { name: "Add server" }));
    await flush();
    expect(api.registries.mcp.add).toHaveBeenCalledWith({
      name: "notion", transport: "http", url: "https://mcp.notion.com/mcp", headers: {}, auth_env: [],
    });
  });

  it("OAuth start sends the browser to the authorize URL", async () => {
    const assign = vi.spyOn(browser, "assign").mockImplementation(() => {});
    const { api } = renderApp(<RegistriesDialog open onClose={() => {}} />);
    await screen.findByRole("listitem", { name: "MCP server github" });
    fireEvent.click(within(server("github")).getByRole("button", { name: "Connect with OAuth" }));
    await flush();
    expect(api.registries.mcp.oauthStart).toHaveBeenCalledWith("github", { return_to: window.location.href });
    expect(assign).toHaveBeenCalledWith(fixture("oauthStart").authorize_url);
  });

  it("remove asks first and reports processes that still use the server", async () => {
    const api = createFakeApi({
      registries: { mcp: { remove: async () => ({ removed: true, referenced_by: ["process_supplier_invoice"] }) } },
    });
    renderApp(<RegistriesDialog open onClose={() => {}} />, { api });
    await screen.findByRole("listitem", { name: "MCP server github" });
    fireEvent.click(within(server("github")).getByRole("button", { name: "Remove" }));
    expect(api.registries.mcp.remove).not.toHaveBeenCalled();
    fireEvent.click(within(server("github")).getByRole("button", { name: "Confirm remove" }));
    await flush();
    expect(api.registries.mcp.remove).toHaveBeenCalledWith("github");
    expect(invalidated).toContain("registries:mcp");
    expect(screen.getByRole("status").textContent).toBe(
      "Removed github. Still used by process_supplier_invoice: their steps fail with a config error until it is added again.",
    );
  });

  it("Done closes", async () => {
    const onClose = vi.fn();
    renderApp(<RegistriesDialog open onClose={onClose} />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});

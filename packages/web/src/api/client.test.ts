import { renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { resetConnection, useConnection } from "../state/connection";
import { ApiError, createApi, pidPath, request } from "./client";

type FetchMock = Mock<(input: string, init?: RequestInit) => Promise<Response>>;

let fetchMock: FetchMock;

function json(status: number, body: unknown, statusText = ""): Response {
  return new Response(JSON.stringify(body), { status, statusText, headers: { "Content-Type": "application/json" } });
}

/** [method, url, parsed JSON body | raw body] of every fetch call. */
function calls(): [string, string, unknown][] {
  return fetchMock.mock.calls.map(([url, init]) => {
    const body = init?.body;
    return [init?.method ?? "GET", url, typeof body === "string" ? JSON.parse(body) : body];
  });
}

beforeEach(() => {
  fetchMock = vi.fn(async () => json(200, {}));
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetConnection();
});

describe("request", () => {
  it("sends JSON and parses the response", async () => {
    fetchMock.mockResolvedValueOnce(json(201, { id: "p1" }));
    await expect(request("POST", "/api/processes", { id: "p1" })).resolves.toEqual({ id: "p1" });
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.method).toBe("POST");
    expect(init?.headers).toEqual({ Accept: "application/json", "Content-Type": "application/json" });
    expect(init?.body).toBe('{"id":"p1"}');
  });

  it("sends no body and no content type without a body; passes keepalive and signal", async () => {
    const controller = new AbortController();
    await request("GET", "/api/meta", undefined, { keepalive: true, signal: controller.signal });
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.body).toBeUndefined();
    expect(init?.headers).toEqual({ Accept: "application/json" });
    expect(init?.keepalive).toBe(true);
    expect(init?.signal).toBe(controller.signal);
  });

  it("resolves an empty 204 to undefined", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(request("DELETE", "/api/chats/c1")).resolves.toBeUndefined();
  });

  it("maps the error envelope to ApiError(status, code, message, details)", async () => {
    fetchMock.mockResolvedValueOnce(json(409, {
      error: { code: "dirty_tree", message: "workspace has uncommitted changes", details: { paths: ["a.yaml"] }, hint: "commit" },
    }));
    const err = await request("POST", "/api/processes/p/build", {}).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({
      status: 409, code: "dirty_tree", message: "workspace has uncommitted changes", details: { paths: ["a.yaml"] },
    });
  });

  it("maps FastAPI's 422 detail list to code invalid with the first message", async () => {
    const detail = [{ loc: ["body", "id"], msg: "Field required", type: "missing" }, { loc: ["body", "x"], msg: "second" }];
    fetchMock.mockResolvedValueOnce(json(422, { detail }));
    const err = await request("POST", "/api/processes", {}).catch((e: unknown) => e);
    expect(err).toMatchObject({ status: 422, code: "invalid", message: "Field required", details: detail });
  });

  it("maps envelope-less errors by status: a route 404 is not_found, others http_<status>", async () => {
    fetchMock.mockResolvedValueOnce(json(404, { detail: "Not Found" }));
    await expect(request("GET", "/api/nope")).rejects.toMatchObject({ status: 404, code: "not_found", message: "Not Found" });
    fetchMock.mockResolvedValueOnce(new Response("upstream down", { status: 502, statusText: "Bad Gateway" }));
    await expect(request("GET", "/api/meta")).rejects.toMatchObject({
      status: 502, code: "http_502", message: "Bad Gateway", details: "upstream down",
    });
  });

  it("turns a fetch TypeError into ApiError(0, network) and reports the connection offline", async () => {
    const connection = renderHook(() => useConnection());
    expect(connection.result.current.online).toBe(true);
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const err = await request("GET", "/api/meta").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 0, code: "network" });
    expect(connection.result.current).toEqual({ online: false, retryInMs: 1000 });
  });

  it("rethrows an abort unchanged", async () => {
    const abort = new DOMException("aborted", "AbortError");
    fetchMock.mockRejectedValueOnce(abort);
    await expect(request("GET", "/api/meta")).rejects.toBe(abort);
  });
});

describe("pidPath", () => {
  it("keeps slashes and encodes each segment", () => {
    expect(pidPath("finance/invoices")).toBe("finance/invoices");
    expect(pidPath("a b/c?d#e")).toBe("a%20b/c%3Fd%23e");
    expect(pidPath("process_supplier_invoice")).toBe("process_supplier_invoice");
  });
});

describe("createApi routes", () => {
  it("processes", async () => {
    const api = createApi();
    await api.processes.list("", []);
    await api.processes.list("invoice pdf", ["design", "released"]);
    await api.processes.get("finance/invoices");
    await api.processes.create({ id: "finance/invoices", goal: "g" });
    await api.processes.design("finance/invoices");
    await api.processes.save("finance/invoices", { writes: [], commit: { reason: "blur", summary: "s" } }, { keepalive: true });
    await api.processes.compile("finance/invoices");
    await api.processes.build("finance/invoices");
    await api.processes.builds("finance/invoices");
    await api.processes.iface("finance/invoices");
    await api.processes.iface("finance/invoices", "abc123");
    expect(calls()).toEqual([
      ["GET", "/api/processes", undefined],
      ["GET", "/api/processes?q=invoice+pdf&flags=design%2Creleased", undefined],
      ["GET", "/api/processes/finance/invoices", undefined],
      ["POST", "/api/processes", { id: "finance/invoices", goal: "g" }],
      ["GET", "/api/processes/finance/invoices/design", undefined],
      ["POST", "/api/processes/finance/invoices/design", { writes: [], commit: { reason: "blur", summary: "s" } }],
      ["POST", "/api/processes/finance/invoices/compile", {}],
      ["POST", "/api/processes/finance/invoices/build", {}],
      ["GET", "/api/processes/finance/invoices/builds", undefined],
      ["GET", "/api/processes/finance/invoices/interface", undefined],
      ["GET", "/api/processes/finance/invoices/interface?commit=abc123", undefined],
    ]);
    expect(fetchMock.mock.calls[5]?.[1]?.keepalive).toBe(true);
  });

  it("expressions, steps, providers, registries", async () => {
    const api = createApi();
    const check = { process_id: "p", process: {}, loc: ["edges", 0], expr: "x", scope: true };
    await api.expressions.validate(check);
    await api.steps.list();
    await api.providers.list();
    await api.providers.add({ name: "anthropic", tiers: { cheap: "claude-haiku-4-5" } });
    await api.providers.remove("anthropic");
    await api.registries.mcp.list();
    await api.registries.mcp.add({ name: "github", transport: "http", url: "https://x" });
    await api.registries.mcp.remove("git hub");
    await api.registries.mcp.oauthStart("github", { return_to: "/" });
    expect(calls()).toEqual([
      ["POST", "/api/expressions/validate", check],
      ["GET", "/api/steps", undefined],
      ["GET", "/api/providers", undefined],
      ["POST", "/api/providers", { name: "anthropic", tiers: { cheap: "claude-haiku-4-5" } }],
      ["DELETE", "/api/providers/anthropic", undefined],
      ["GET", "/api/registries/mcp", undefined],
      ["POST", "/api/registries/mcp", { name: "github", transport: "http", url: "https://x" }],
      ["DELETE", "/api/registries/mcp/git%20hub", undefined],
      ["POST", "/api/registries/mcp/github/oauth/start", { return_to: "/" }],
    ]);
  });

  it("jobs and chats", async () => {
    const api = createApi();
    await api.jobs.list({});
    await api.jobs.list({ process_id: "finance/invoices", active: true });
    await api.jobs.get("job_1");
    await api.jobs.logs("job_1", 214);
    await api.jobs.answer("job_1", { question_id: "q1", text: "GBP" });
    await api.jobs.integrate("job_1");
    await api.jobs.cancel("job_1");
    await api.chats.list();
    await api.chats.create({});
    await api.chats.get("chat_1");
    await api.chats.update("chat_1", { title: "t" });
    await api.chats.remove("chat_1");
    await api.chats.send("chat_1", { text: "hi", acting_on: "p", client_id: "c" });
    await api.chats.cancel("chat_1");
    expect(calls()).toEqual([
      ["GET", "/api/jobs", undefined],
      ["GET", "/api/jobs?process_id=finance%2Finvoices&active=true", undefined],
      ["GET", "/api/jobs/job_1", undefined],
      ["GET", "/api/jobs/job_1/logs?offset=214", undefined],
      ["POST", "/api/jobs/job_1/answers", { question_id: "q1", text: "GBP" }],
      ["POST", "/api/jobs/job_1/integrate", {}],
      ["POST", "/api/jobs/job_1/cancel", {}],
      ["GET", "/api/chats", undefined],
      ["POST", "/api/chats", {}],
      ["GET", "/api/chats/chat_1", undefined],
      ["PATCH", "/api/chats/chat_1", { title: "t" }],
      ["DELETE", "/api/chats/chat_1", undefined],
      ["POST", "/api/chats/chat_1/messages", { text: "hi", acting_on: "p", client_id: "c" }],
      ["POST", "/api/chats/chat_1/cancel", {}],
    ]);
    expect(api.chats.eventsUrl("chat_1", 41)).toBe("/api/chats/chat_1/events?since=41");
  });

  it("runs, uploads and releases", async () => {
    const api = createApi();
    await api.runs.create({ process_id: "p", target: { kind: "local" }, inputs: { pdf_path: "a.pdf" } });
    await api.runs.list({ process_id: "p", limit: 20 });
    await api.runs.list({ release_id: "rel_1" });
    await api.runs.get("run_1");
    await api.releases.list("finance/invoices");
    await api.releases.update("rel_1", { enabled: false });
    await api.releases.remove("rel_1");
    await api.releases.trigger("rel_1", { pdf_path: "/inbox/a.pdf" });
    await api.releases.envCheck("rel_1");
    expect(calls()).toEqual([
      ["POST", "/api/runs", { process_id: "p", target: { kind: "local" }, inputs: { pdf_path: "a.pdf" } }],
      ["GET", "/api/runs?process_id=p&limit=20", undefined],
      ["GET", "/api/runs?release_id=rel_1", undefined],
      ["GET", "/api/runs/run_1", undefined],
      ["GET", "/api/releases?process_id=finance%2Finvoices", undefined],
      ["PATCH", "/api/releases/rel_1", { enabled: false }],
      ["DELETE", "/api/releases/rel_1", undefined],
      ["POST", "/api/releases/rel_1/trigger", { inputs: { pdf_path: "/inbox/a.pdf" } }],
      ["GET", "/api/releases/rel_1/env-check", undefined],
    ]);
    expect(api.runs.eventsUrl("run_1")).toBe("/api/runs/run_1/events?since=0");
    expect(api.runs.eventsUrl("run_1", 7)).toBe("/api/runs/run_1/events?since=7");
  });

  it("uploads the raw file with its name and type; non-ASCII names are percent-encoded", async () => {
    const api = createApi();
    fetchMock.mockImplementation(async () => json(201, { path: "/ws/.wynd/uploads/abc/inv 1.pdf" }));
    const pdf = new File(["%PDF-1.4"], "inv 1.pdf", { type: "application/pdf" });
    await expect(api.uploads.put(pdf)).resolves.toEqual({ path: "/ws/.wynd/uploads/abc/inv 1.pdf" });
    await api.uploads.put(new File(["x"], "façture.bin"));
    const [first, second] = fetchMock.mock.calls;
    expect(first?.[0]).toBe("/api/uploads");
    expect(first?.[1]?.method).toBe("POST");
    expect(first?.[1]?.body).toBe(pdf);
    expect(first?.[1]?.headers).toMatchObject({ "X-Wynd-Filename": "inv 1.pdf", "Content-Type": "application/pdf" });
    expect(second?.[1]?.headers).toMatchObject({
      "X-Wynd-Filename": "fa%C3%A7ture.bin", "Content-Type": "application/octet-stream",
    });
  });
});

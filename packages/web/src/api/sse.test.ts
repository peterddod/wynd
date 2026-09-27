import { afterEach, describe, expect, it, vi } from "vitest";
import { FakeEventSource } from "../test/fakeEventSource";
import { chatEventFrames, fixture, runEventFrames } from "../test/fixtures";
import { openEventStream, setEventSourceImpl } from "./sse";

function source(): FakeEventSource {
  const last = FakeEventSource.last();
  if (last === undefined) throw new Error("no EventSource was opened");
  return last;
}

afterEach(() => {
  setEventSourceImpl(FakeEventSource as unknown as typeof EventSource);
});

describe("openEventStream", () => {
  it("opens the URL with the global EventSource and dispatches named events with parsed data and the event id", () => {
    const item = vi.fn();
    const turn = vi.fn();
    openEventStream("/api/chats/c1/events?since=40", { events: { item, turn } });
    expect(source().url).toBe("/api/chats/c1/events?since=40");
    source().play(chatEventFrames());
    const frames = chatEventFrames();
    expect(item.mock.calls).toEqual(frames.filter((f) => f.event === "item").map((f) => [f.data, f.id]));
    expect(turn.mock.calls).toEqual(frames.filter((f) => f.event === "turn").map((f) => [f.data, f.id]));
    expect(item.mock.calls[0]?.[0]).toEqual({ item: fixture("sendResult").item });
  });

  it("an empty event id is null", () => {
    const trace = vi.fn();
    openEventStream("/x", { events: { trace } });
    source().emit("trace", { seq: 1 });
    expect(trace).toHaveBeenCalledWith({ seq: 1 }, null);
  });

  it("end runs its handler then closes the stream; later events are ignored", () => {
    const trace = vi.fn();
    const end = vi.fn();
    openEventStream("/api/runs/r1/events?since=0", { events: { trace, end } });
    const frames = runEventFrames();
    source().play(frames);
    expect(trace).toHaveBeenCalledTimes(frames.length - 1);
    expect(trace.mock.calls.map(([ev]) => (ev as { seq: number }).seq)).toEqual(fixture("runEvents").map((e) => e.seq));
    expect(end).toHaveBeenCalledWith({}, null);
    expect(source().readyState).toBe(FakeEventSource.CLOSED);
    source().emit("trace", { seq: 99 }, 99);
    expect(trace).toHaveBeenCalledTimes(frames.length - 1);
  });

  it("end closes the stream even without an end handler", () => {
    openEventStream("/x", { events: { trace: vi.fn() } });
    source().emit("end", {});
    expect(source().readyState).toBe(FakeEventSource.CLOSED);
  });

  it("drops an event with malformed JSON and keeps going", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const item = vi.fn();
    openEventStream("/x", { events: { item } });
    source().emit("item", "{not json", 5);
    source().emit("item", { ok: 1 }, 6);
    expect(item.mock.calls).toEqual([[{ ok: 1 }, "6"]]);
    expect(warn).toHaveBeenCalledOnce();
  });

  it("close() stops delivery and closes the source", () => {
    const item = vi.fn();
    const close = openEventStream("/x", { events: { item } });
    source().emit("item", { n: 1 }, 1);
    close();
    expect(source().readyState).toBe(FakeEventSource.CLOSED);
    source().open();
    source().dispatchEvent(new MessageEvent("item", { data: '{"n":2}', lastEventId: "2" }));
    expect(item.mock.calls).toEqual([[{ n: 1 }, "1"]]);
  });

  it("reports open and error (the reconnecting state)", () => {
    const onOpen = vi.fn();
    const onError = vi.fn();
    openEventStream("/x", { events: {}, onOpen, onError });
    source().open();
    source().error();
    expect(onOpen).toHaveBeenCalledOnce();
    expect(onError).toHaveBeenCalledOnce();
    expect(source().readyState).toBe(FakeEventSource.CONNECTING);
  });

  it("setEventSourceImpl replaces the class", () => {
    const urls: string[] = [];
    class Recording extends FakeEventSource {
      constructor(url: string | URL) {
        super(url);
        urls.push(String(url));
      }
    }
    setEventSourceImpl(Recording as unknown as typeof EventSource);
    openEventStream("/api/runs/r2/events?since=3", { events: {} });
    expect(urls).toEqual(["/api/runs/r2/events?since=3"]);
    expect(source()).toBeInstanceOf(Recording);
  });
});

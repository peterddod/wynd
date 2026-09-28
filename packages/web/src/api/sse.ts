// SSE over native EventSource (`$DRAFTS/07 §4.5`): named events with JSON data, native reconnect with
// Last-Event-ID (the server sends `retry: 2000`). The EventSource class is read at call time (default
// `globalThis.EventSource`): the test setup installs `FakeEventSource` there.

export type SseHandler = (data: unknown, lastEventId: string | null) => void;

export interface SseOptions {
  events: Record<string, SseHandler>;   // named events: item, delta, turn, reset, trace, end
  onOpen?(): void;
  onError?(): void;                     // readyState is CONNECTING while the browser reconnects
}

let impl: typeof EventSource | null = null;

/** Opens the stream; returns close(). `end` closes it after its handler runs. */
export function openEventStream(url: string, opts: SseOptions): () => void {
  const Source = impl ?? globalThis.EventSource;
  const source = new Source(url);
  let closed = false;
  const close = (): void => {
    closed = true;
    source.close();
  };

  const names = new Set([...Object.keys(opts.events), "end"]);
  for (const name of names) {
    source.addEventListener(name, (ev) => {
      if (closed) return;
      const handler = opts.events[name];
      try {
        if (handler !== undefined) deliver(url, name, ev as MessageEvent, handler);
      } finally {
        if (name === "end") close();
      }
    });
  }
  source.addEventListener("open", () => {
    if (!closed) opts.onOpen?.();
  });
  source.addEventListener("error", () => {
    if (!closed) opts.onError?.();
  });
  return close;
}

function deliver(url: string, name: string, ev: MessageEvent, handler: SseHandler): void {
  let data: unknown;
  try {
    data = JSON.parse(String(ev.data));
  } catch {
    console.warn(`dropped SSE '${name}' event with malformed data from ${url}`);
    return;
  }
  handler(data, ev.lastEventId === "" ? null : ev.lastEventId);
}

/** Test seam: replaces the EventSource class used by `openEventStream`. */
export function setEventSourceImpl(next: typeof EventSource): void {
  impl = next;
}

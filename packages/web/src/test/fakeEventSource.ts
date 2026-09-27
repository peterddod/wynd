// In-memory EventSource for tests (`$DRAFTS/07 §17.1`). setup.ts installs it as globalThis.EventSource; tests reach
// the instances the code under test opened with `FakeEventSource.instances()` / `.last()` and drive them.

export interface SseFrame {
  id: string | null;
  event: string;
  data: unknown;                     // parsed JSON; data that is not JSON stays the raw string
}

export class FakeEventSource extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  readonly CONNECTING = 0;
  readonly OPEN = 1;
  readonly CLOSED = 2;

  private static opened: FakeEventSource[] = [];

  readonly url: string;
  readonly withCredentials: boolean;
  readyState: number = FakeEventSource.CONNECTING;
  onopen: ((ev: Event) => unknown) | null = null;
  onmessage: ((ev: MessageEvent) => unknown) | null = null;
  onerror: ((ev: Event) => unknown) | null = null;

  constructor(url: string | URL, init?: EventSourceInit) {
    super();
    this.url = String(url);
    this.withCredentials = init?.withCredentials ?? false;
    FakeEventSource.opened.push(this);
  }

  /** Every instance created since the last reset, in order. */
  static instances(): FakeEventSource[] {
    return [...FakeEventSource.opened];
  }

  static last(): FakeEventSource | undefined {
    return FakeEventSource.opened.at(-1);
  }

  static reset(): void {
    FakeEventSource.opened = [];
  }

  /** The connection is established. */
  open(): void {
    this.readyState = FakeEventSource.OPEN;
    const ev = new Event("open");
    this.onopen?.(ev);
    this.dispatchEvent(ev);
  }

  /** Delivers one event: `data` is JSON-encoded unless it is already a string. A CONNECTING source opens first;
   *  a closed one ignores it. */
  emit(event: string, data: unknown, id?: string | number | null): void {
    if (this.readyState === FakeEventSource.CLOSED) return;
    if (this.readyState === FakeEventSource.CONNECTING) this.open();
    const text = typeof data === "string" ? data : JSON.stringify(data);
    const ev = new MessageEvent(event, { data: text, lastEventId: id == null ? "" : String(id) });
    if (event === "message") this.onmessage?.(ev);
    this.dispatchEvent(ev);
  }

  /** Delivers frames in order (stops early if a handler closes the source). */
  play(frames: SseFrame[]): void {
    for (const f of frames) this.emit(f.event, f.data, f.id);
  }

  /** A dropped connection: like the native source, it goes back to CONNECTING (and would reconnect). */
  error(): void {
    if (this.readyState === FakeEventSource.CLOSED) return;
    this.readyState = FakeEventSource.CONNECTING;
    const ev = new Event("error");
    this.onerror?.(ev);
    this.dispatchEvent(ev);
  }

  close(): void {
    this.readyState = FakeEventSource.CLOSED;
  }
}

/** Parses an SSE transcript (`id:`, `event:`, `data:` lines; blank line ends a frame; `retry:` and comments skipped). */
export function parseSse(text: string): SseFrame[] {
  const frames: SseFrame[] = [];
  let id: string | null = null;
  let event = "message";
  let data: string[] = [];
  for (const line of [...text.split("\n"), ""]) {
    if (line === "") {
      if (data.length > 0) frames.push({ id, event, data: parseData(data.join("\n")) });
      id = null;
      event = "message";
      data = [];
      continue;
    }
    if (line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    switch (field) {
      case "id":
        id = value;
        break;
      case "event":
        event = value;
        break;
      case "data":
        data.push(value);
        break;
    }
  }
  return frames;
}

function parseData(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

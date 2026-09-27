// SSE over native EventSource (`$DRAFTS/07 §4.5`). Stub from WEB-SCAFFOLD; WEB-CORE implements it.
// The implementation must read the EventSource class at call time (default `globalThis.EventSource`): the test
// setup installs `FakeEventSource` there before any test module is imported.

export type SseHandler = (data: unknown, lastEventId: string | null) => void;

export interface SseOptions {
  events: Record<string, SseHandler>;   // named events: item, delta, turn, reset, trace, end
  onOpen?(): void;
  onError?(): void;
}

/** Opens the stream; returns close(). `end` closes it after its handler runs. */
export function openEventStream(url: string, opts: SseOptions): () => void {
  throw new Error("not implemented");
}

/** Test seam: replaces the EventSource class used by `openEventStream`. */
export function setEventSourceImpl(impl: typeof EventSource): void {
  throw new Error("not implemented");
}

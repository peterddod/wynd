// Vitest setup (vite.config.ts `test.setupFiles`; `$DRAFTS/07 §17.1`): RTL cleanup, the jsdom shims React Flow
// needs, FakeEventSource as the global EventSource, in-memory Web Storage emptied before every test, and the
// module-level web state (query cache, toasts, connection, theme) reset after every test.
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";
import { resetConnection } from "../state/connection";
import { resetQueryCache } from "../state/query";
import { setTheme } from "../state/theme";
import { clearToasts } from "../state/toasts";
import { FakeEventSource } from "./fakeEventSource";

class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

/** React Flow reads the zoom from `new DOMMatrixReadOnly(transform).m22`. */
class DOMMatrixReadOnlyStub {
  readonly m22: number;

  constructor(transform?: string) {
    const scale = transform?.match(/scale\(([^)]+)\)/)?.[1];
    this.m22 = scale === undefined ? 1 : parseFloat(scale);
  }
}

/** Deterministic Web Storage: Node's own `localStorage` global can shadow jsdom's and be unusable without a file. */
class MemoryStorage {
  private items = new Map<string, string>();

  get length(): number {
    return this.items.size;
  }

  clear(): void {
    this.items.clear();
  }

  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }

  key(index: number): string | null {
    return [...this.items.keys()][index] ?? null;
  }

  removeItem(key: string): void {
    this.items.delete(key);
  }

  setItem(key: string, value: string): void {
    this.items.set(key, String(value));
  }
}

function install(name: string, value: unknown): void {
  for (const target of new Set<object>([globalThis, window])) {
    Object.defineProperty(target, name, { value, configurable: true, writable: true });
  }
}

install("ResizeObserver", ResizeObserverStub);
install("DOMMatrixReadOnly", DOMMatrixReadOnlyStub);
install("EventSource", FakeEventSource);
install("localStorage", new MemoryStorage());
install("sessionStorage", new MemoryStorage());

Object.defineProperties(HTMLElement.prototype, {
  offsetWidth: {
    configurable: true,
    get(this: HTMLElement): number {
      return parseFloat(this.style.width) || 1;
    },
  },
  offsetHeight: {
    configurable: true,
    get(this: HTMLElement): number {
      return parseFloat(this.style.height) || 1;
    },
  },
});

Object.defineProperty(SVGElement.prototype, "getBBox", {
  configurable: true,
  value: (): DOMRect => ({ x: 0, y: 0, width: 0, height: 0, top: 0, left: 0, right: 0, bottom: 0, toJSON: () => ({}) }),
});

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  FakeEventSource.reset();
  resetQueryCache();
  clearToasts();
  resetConnection();
  setTheme("system");
});

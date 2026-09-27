// Example and run value parsing (`$DRAFTS/07 §7.11`). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { Interface, Json, JsonSchema } from "../api/types";

/** valid JSON -> parsed; else the raw string */
export function parseLoose(text: string): Json {
  throw new Error("not implemented");
}

/** strings raw; others JSON.stringify */
export function formatLoose(v: Json): string {
  throw new Error("not implemented");
}

export function parseTyped(text: string, s: JsonSchema | null): { ok: true; value: Json } | { ok: false; error: string } {
  throw new Error("not implemented");
}

/** 'Given invoice_text = "Dear customer…" → done with invoice_number = "INV-1042", total = 1200.5' */
export function exampleSentence(ex: Json, iface: Interface | null): string {
  throw new Error("not implemented");
}

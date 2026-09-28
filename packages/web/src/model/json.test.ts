import { describe, expect, it } from "vitest";
import { deepEqual, getIn, renameKey, setIn, setKeyAfter } from "./json";

describe("getIn", () => {
  it("walks objects and arrays and returns undefined for missing paths", () => {
    const doc = { a: [{ b: 1 }, null], c: { d: false } };
    expect(getIn(doc, ["a", 0, "b"])).toBe(1);
    expect(getIn(doc, ["a", 1])).toBeNull();
    expect(getIn(doc, ["c", "d"])).toBe(false);
    expect(getIn(doc, ["a", "b"])).toBeUndefined();
    expect(getIn(doc, ["c", 0])).toBeUndefined();
    expect(getIn(doc, ["x", "y"])).toBeUndefined();
    expect(getIn(doc, ["toString"])).toBeUndefined();
    expect(getIn(doc, [])).toBe(doc);
  });
});

describe("setIn", () => {
  it("keeps the position of an existing key and appends a new one", () => {
    const doc = { a: 1, b: 2, c: 3 };
    const out = setIn(doc, ["b"], 20);
    expect(Object.keys(out as object)).toEqual(["a", "b", "c"]);
    expect(out).toEqual({ a: 1, b: 20, c: 3 });
    expect(Object.keys(setIn(doc, ["z"], 0) as object)).toEqual(["a", "b", "c", "z"]);
    expect(doc).toEqual({ a: 1, b: 2, c: 3 });
  });

  it("deletes keys and array items with undefined", () => {
    expect(setIn({ a: 1, b: 2 }, ["a"], undefined)).toEqual({ b: 2 });
    expect(setIn({ l: [1, 2, 3] }, ["l", 1], undefined)).toEqual({ l: [1, 3] });
    const doc = { a: 1 };
    expect(setIn(doc, ["x", "y"], undefined)).toBe(doc);
  });

  it("creates missing parents and shares untouched subtrees", () => {
    const doc = { keep: { deep: [1] }, edit: { x: 1 } };
    const out = setIn(doc, ["edit", "y", "z"], "v") as typeof doc & { edit: { y: { z: string } } };
    expect(out.edit).toEqual({ x: 1, y: { z: "v" } });
    expect(out.keep).toBe(doc.keep);
    expect(setIn(null, ["l", 0], "a")).toEqual({ l: ["a"] });
  });

  it("returns the same reference when nothing changes", () => {
    const doc = { a: { b: 1 }, l: [1] };
    expect(setIn(doc, ["a", "b"], 1)).toBe(doc);
    expect(setIn(doc, ["l", 0], 1)).toBe(doc);
    expect(setIn(doc, ["missing"], undefined)).toBe(doc);
  });
});

describe("renameKey / setKeyAfter / deepEqual", () => {
  it("renames in place", () => {
    const out = renameKey({ a: 1, b: 2, c: 3 }, "b", "x");
    expect(Object.entries(out)).toEqual([["a", 1], ["x", 2], ["c", 3]]);
    const same = { a: 1 };
    expect(renameKey(same, "missing", "x")).toBe(same);
  });

  it("inserts a key after another", () => {
    expect(Object.keys(setKeyAfter({ step: "a", with: {} }, "step", "when", "x"))).toEqual(["step", "when", "with"]);
    expect(Object.keys(setKeyAfter({ with: {} }, "step", "when", "x"))).toEqual(["with", "when"]);
    expect(setKeyAfter({ step: "a", when: "y", with: {} }, "step", "when", "x")).toEqual({ step: "a", when: "x", with: {} });
  });

  it("compares structurally, ignoring key order", () => {
    expect(deepEqual({ a: [1, { b: null }], c: "x" }, { c: "x", a: [1, { b: null }] })).toBe(true);
    expect(deepEqual({ a: [1] }, { a: [1, 2] })).toBe(false);
    expect(deepEqual({ a: 1 }, { a: 1, b: undefined as never })).toBe(false);
    expect(deepEqual([1], { 0: 1 })).toBe(false);
    expect(deepEqual(null, undefined)).toBe(false);
  });
});

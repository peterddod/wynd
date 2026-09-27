import { describe, expect, it } from "vitest";
import { fixture } from "../test/fixtures";
import { layoutInput, toFlow } from "./graph";
import { GAP_X, GAP_Y, layout } from "./layout";

function dogfoodLayout(): Record<string, { x: number; y: number }> {
  const design = fixture("design");
  const flow = toFlow({ process: design.process_file.doc, protos: {}, steps: design.steps, issues: null, readOnly: false });
  const { nodes, edges } = layoutInput(flow);
  return layout(nodes, edges);
}

const COLUMN = 220 + GAP_X;

function layerOf(pos: Record<string, { x: number; y: number }>): Record<string, number> {
  return Object.fromEntries(Object.entries(pos).map(([id, p]) => [id, p.x / COLUMN]));
}

describe("layout", () => {
  it("places the dogfood in the expected layers", () => {
    expect(layerOf(dogfoodLayout())).toEqual({
      in: 0, "s:read": 1, "s:extract": 2, "s:validate": 3, "s:save": 4, "s:fix": 4, "s:escalate": 4,
      "x:done": 5, "x:not_an_invoice": 5, "x:needs_review": 5,
    });
  });

  it("gives deterministic coordinates, the same on every run", () => {
    const first = dogfoodLayout();
    expect(dogfoodLayout()).toEqual(first);
    const rounded = Object.fromEntries(Object.entries(first).map(([id, p]) => [id, [Math.round(p.x), Math.round(p.y)]]));
    expect(rounded).toMatchInlineSnapshot(`
      {
        "in": [
          0,
          -22,
        ],
        "s:escalate": [
          1280,
          88,
        ],
        "s:extract": [
          640,
          -63,
        ],
        "s:fix": [
          1280,
          -192,
        ],
        "s:read": [
          320,
          -52,
        ],
        "s:save": [
          1280,
          -52,
        ],
        "s:validate": [
          960,
          -52,
        ],
        "x:done": [
          1600,
          -22,
        ],
        "x:needs_review": [
          1600,
          58,
        ],
        "x:not_an_invoice": [
          1600,
          -102,
        ],
      }
    `);
  });

  it("treats fix -> validate as the back edge (validate stays left of fix)", () => {
    const pos = dogfoodLayout();
    expect(pos["s:validate"]!.x).toBeLessThan(pos["s:fix"]!.x);
  });

  it("centres each layer and stacks nodes with the gap", () => {
    const pos = layout(
      [{ id: "a", w: 10, h: 40, order: 0 }, { id: "b", w: 10, h: 60, order: 1 }, { id: "c", w: 10, h: 20, order: 2 }],
      [{ source: "a", target: "c", order: 0 }, { source: "b", target: "c", order: 1 }],
    );
    const total = 40 + 60 + GAP_Y;
    expect(pos.a).toEqual({ x: 0, y: -total / 2 });
    expect(pos.b).toEqual({ x: 0, y: -total / 2 + 40 + GAP_Y });
    expect(pos.c).toEqual({ x: COLUMN, y: -10 });
  });

  it("still places unreachable steps and puts every terminal in the last layer", () => {
    const pos = layout(
      [
        { id: "in", w: 180, h: 44, order: -1, rank: "first" },
        { id: "s:a", w: 220, h: 82, order: 0 },
        { id: "s:b", w: 220, h: 82, order: 1 },
        { id: "s:orphan", w: 220, h: 82, order: 2 },
        { id: "x:done", w: 160, h: 44, order: 3, rank: "last" },
        { id: "x:early", w: 160, h: 44, order: 4, rank: "last" },
      ],
      [
        { source: "in", target: "s:a", order: -1 },
        { source: "s:a", target: "s:b", order: 0 },
        { source: "s:a", target: "x:early", order: 1 },
        { source: "s:b", target: "x:done", order: 2 },
        { source: "s:b", target: "s:b", order: 3 },
      ],
    );
    expect(Object.keys(pos).sort()).toEqual(["in", "s:a", "s:b", "s:orphan", "x:done", "x:early"]);
    expect(pos["x:done"]!.x).toBe(3 * COLUMN);
    expect(pos["x:early"]!.x).toBe(3 * COLUMN);
    expect(pos["s:orphan"]!.x).toBe(0);
  });
});

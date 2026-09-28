import { describe, expect, it } from "vitest";
import type { Issue, Loc } from "../api/types";
import { issuesAt, selectionForIssue, selectionParam } from "./issues";

const PROCESS = "processes/p/process.yaml";
const PROTO = "processes/p/proto/extract.yaml";
const d = { processPath: PROCESS, stepsByProto: { [PROTO]: ["extract", "extract_again"] } };

function issue(loc: Loc, file: string | null = PROCESS): Issue {
  return { severity: "error", code: "E-X", message: "m", file, loc, span: null };
}

describe("selectionForIssue", () => {
  it("maps each loc rule to its selection", () => {
    expect(selectionForIssue(issue(["steps", "fix", "use"]), d)).toEqual({ kind: "step", step: "fix" });
    expect(selectionForIssue(issue(["edges", 3, "to", 1, "when"]), d)).toEqual({ kind: "branch", e: 3, b: 1 });
    expect(selectionForIssue(issue(["edges", 3, "from"]), d)).toEqual({ kind: "edge", e: 3 });
    expect(selectionForIssue(issue(["outputs", "done", "record"]), d)).toEqual({ kind: "exit", exit: "done" });
    expect(selectionForIssue(issue(["inputs", "pdf_path"]), d)).toEqual({ kind: "inputs" });
    expect(selectionForIssue(issue(["entry"]), d)).toEqual({ kind: "inputs" });
    expect(selectionForIssue(issue(["provider"]), d)).toEqual({ kind: "process" });
    expect(selectionForIssue(issue([]), d)).toEqual({ kind: "process" });
    expect(selectionForIssue(issue(["edges", 2], null), d)).toEqual({ kind: "edge", e: 2 });
  });

  it("maps a proto file issue to the first step using that proto", () => {
    expect(selectionForIssue(issue(["outputs", "done"], PROTO), d)).toEqual({ kind: "step", step: "extract" });
    expect(selectionForIssue(issue(["outputs"], "shared/steps/x/proto.yaml"), d)).toEqual({ kind: "process" });
  });

  it("turns selections into URL sel values", () => {
    expect(selectionParam({ kind: "step", step: "fix" })).toBe("s:fix");
    expect(selectionParam({ kind: "branch", e: 3, b: 1 })).toBe("b:3:1");
    expect(selectionParam({ kind: "edge", e: 3 })).toBe("b:3:0");
    expect(selectionParam({ kind: "exit", exit: "done" })).toBe("x:done");
    expect(selectionParam({ kind: "inputs" })).toBe("in");
    expect(selectionParam({ kind: "process" })).toBeNull();
  });
});

describe("issuesAt", () => {
  it("prefix-matches locs within one file", () => {
    const issues = [issue(["edges", 3, "to", 1, "when"]), issue(["edges", 3, "to", 0]), issue(["edges", 3, "to", 1], PROTO)];
    expect(issuesAt(issues, PROCESS, ["edges", 3, "to", 1])).toEqual([issues[0]]);
    expect(issuesAt(issues, PROCESS, ["edges", 3])).toHaveLength(2);
    expect(issuesAt(issues, PROCESS, [])).toHaveLength(2);
  });
});

// No selection: plain-language process summary, issue list (click to select), cycle notes (`$DRAFTS/07 §7.7`,
// §7.12). Issues from before a structural edit are listed greyed until the next save response.
import type { ReactElement } from "react";
import { useMeta } from "../../api/context";
import { branchesOnCycles } from "../../model/cycles";
import { getIn } from "../../model/json";
import { formatLoc, selectionForIssue, selectionParam } from "../../model/issues";
import { branchKey, edgesOf, targetString } from "../../model/processDoc";
import { formatLoose } from "../../model/values";
import { useUrlState } from "../../state/url";
import { classes, processInterface, stepsByProto, useDesignState } from "../graph/designHooks";
import { SchemaSummary } from "./SchemaSummary";

export function ProcessOverview(): ReactElement | null {
  const meta = useMeta();
  const s = useDesignState();
  const [, setUrl] = useUrlState();
  if (s === null) return null;
  const doc = s.process.doc;
  const goal = getIn(doc, ["goal"]);
  const issues = s.report?.issues ?? [];
  const byProto = stepsByProto(s);
  const cycles = branchesOnCycles(doc);
  const cycleNotes = edgesOf(doc).flatMap((e) => e.branches.filter((b) => cycles.has(`${e.index}:${b.index}`)).map((b) => {
    const limit = b.limits.max_traversals;
    const value = limit === undefined ? `${meta?.default_max_traversals ?? 10} (auto)` : formatLoose(limit);
    return `${branchKey(e, b.index)} → ${targetString(b.target)}: max_traversals ${value}`;
  }));

  return (
    <div className="wg-overview">
      <h2>{String(getIn(doc, ["name"]) ?? s.processId)}</h2>
      {typeof goal === "string" && <p>{goal}</p>}
      <section aria-label="What this process does">
        <h3>What this process does</h3>
        <SchemaSummary iface={processInterface(s.iface)} />
      </section>
      <section aria-label="Validation">
        <h3>Validation {s.issuesStale && <small className="wg-muted">(from before the last structural edit)</small>}</h3>
        {issues.length === 0 ? <p className="wg-muted">No issues.</p> : (
          <ul className={classes("wg-issues", s.issuesStale && "wg-stale")}>
            {issues.map((issue, i) => (
              <li key={i} className={`wg-issue wg-issue-${issue.severity}`}>
                <button type="button"
                        onClick={() => setUrl({ sel: selectionParam(selectionForIssue(issue, { processPath: s.processPath, stepsByProto: byProto })) })}>
                  <span className="wg-badge">{issue.severity}</span> <span className="wg-mono">{issue.code}</span> {issue.message}
                  {issue.loc.length > 0 && <small className="wg-mono wg-muted"> {formatLoc(issue.loc)}</small>}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      {cycleNotes.length > 0 && (
        <section aria-label="Cycles">
          <h3>Cycles</h3>
          <ul>{cycleNotes.map((n) => <li key={n} className="wg-mono">{n}</li>)}</ul>
        </section>
      )}
    </div>
  );
}

// fast_forward / rebased / pr_branch / noop (`$DRAFTS/07 §9.7`; `Integration` = PLAN §3.18 `IntegrationResult`).
// `pr_url` is always null in v1; `reason` carries the controller's hint (e.g. a `gh pr create` command).
import type { ReactElement } from "react";
import type { Integration } from "../../api/types";
import { CopyButton } from "../common/CopyButton";

export interface IntegrationOutcomeProps {
  integration: Integration;
}

function NowAt({ head }: { head: string | null }): ReactElement | null {
  if (head === null) return null;
  return <>, now at <code title={head}>{head.slice(0, 7)}</code></>;
}

export function IntegrationOutcome({ integration: i }: IntegrationOutcomeProps): ReactElement {
  switch (i.mode) {
    case "fast_forward":
      return (
        <p className="wc-integration" data-mode={i.mode}>
          Fast-forwarded <code>{i.branch}</code> onto <code>{i.target}</code><NowAt head={i.head} />.
        </p>
      );
    case "rebased":
      return (
        <p className="wc-integration" data-mode={i.mode}>
          Rebased onto <code>{i.target}</code> past {i.skipped_commits} unrelated
          commit{i.skipped_commits === 1 ? "" : "s"}<NowAt head={i.head} />.
        </p>
      );
    case "pr_branch": {
      const command = `git switch ${i.branch}`;
      return (
        <div className="wc-integration" data-mode={i.mode}>
          <p>
            Left on branch <code>{i.branch}</code> for review: commits inside this process's closure moved since the
            job started.
          </p>
          {i.conflicts.length > 0 && (
            <ul className="wc-conflicts" aria-label="Conflicting paths">
              {i.conflicts.map((path) => <li key={path}><code>{path}</code></li>)}
            </ul>
          )}
          {i.reason !== null && <p className="wc-muted">{i.reason}</p>}
          {i.pr_url !== null && (
            <p><a href={i.pr_url} target="_blank" rel="noopener noreferrer">Open the pull request</a></p>
          )}
          <p><code>{command}</code> <CopyButton text={command} label="Copy git switch command" /></p>
        </div>
      );
    }
    case "noop":
      return <p className="wc-integration" data-mode={i.mode}>Nothing to integrate.</p>;
  }
}

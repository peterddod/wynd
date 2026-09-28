// Releases tab: release cards + [New release] (`$DRAFTS/07 §10`).
import { useState, type ReactElement } from "react";
import { useApi } from "../../api/context";
import { useQuery } from "../../state/query";
import { ErrorBox } from "../common/ErrorBox";
import { NewReleaseDialog } from "./NewReleaseDialog";
import { ReleaseCard } from "./ReleaseCard";

export interface ReleasesPanelProps {
  processId: string;
}

export function ReleasesPanel({ processId }: ReleasesPanelProps): ReactElement {
  const api = useApi();
  const releases = useQuery(`releases:${processId}`, () => api.releases.list(processId));
  const [creating, setCreating] = useState(false);
  const list = releases.data?.releases;
  return (
    <section className="wo-releases" aria-label="Releases">
      <div className="wo-toolbar">
        <button type="button" onClick={() => setCreating(true)}>New release</button>
      </div>
      <ErrorBox error={releases.error} onRetry={() => void releases.reload()} />
      {list === undefined && releases.loading && <p className="wo-muted">Loading…</p>}
      {list !== undefined && list.length === 0 && (
        <p className="wo-muted">No releases yet. A release is a built image with a trigger and its environment.</p>
      )}
      {list !== undefined && list.length > 0 && (
        <ul className="wo-cards">
          {list.map((r) => <li key={r.id}><ReleaseCard release={r} /></li>)}
        </ul>
      )}
      <NewReleaseDialog open={creating} processId={processId} onClose={() => setCreating(false)} />
    </section>
  );
}

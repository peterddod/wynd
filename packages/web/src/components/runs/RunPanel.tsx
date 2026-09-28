// Runs tab: RunList | RunForm or RunDetail(url.run) (`$DRAFTS/07 §11.1`).
import type { ReactElement } from "react";
import { invalidate } from "../../state/query";
import { useUrlState } from "../../state/url";
import { RunDetail } from "./RunDetail";
import { RunForm } from "./RunForm";
import { RunList } from "./RunList";

export interface RunPanelProps {
  processId: string;
}

export function RunPanel({ processId }: RunPanelProps): ReactElement {
  const [url, setUrl] = useUrlState();
  return (
    <div className="wo-runs">
      <RunList processId={processId} selected={url.run} onSelect={(id) => setUrl({ run: id })} onNew={() => setUrl({ run: null })} />
      <section className="wo-run-main" aria-label={url.run === null ? "New run" : "Run"}>
        {url.run === null ? (
          <RunForm
            processId={processId}
            onStarted={(run) => {
              invalidate(`runs:${processId}`);
              setUrl({ run: run.id });
            }}
          />
        ) : (
          <RunDetail key={url.run} runId={url.run} />
        )}
      </section>
    </div>
  );
}

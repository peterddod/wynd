// step, cause, inputs, partial outputs, trace pointer, kept workspace (`$DRAFTS/07 §11.4`; PLAN §3.8). A child
// process's error (cause `child_process`) nests.
import type { ReactElement } from "react";
import type { ProcessError, StepError } from "../../api/types";
import { JsonView } from "../common/JsonView";

export interface ProcessErrorViewProps {
  error: ProcessError;
}

export function ProcessErrorView({ error }: ProcessErrorViewProps): ReactElement {
  return (
    <section className="wo-perror" aria-label={`Process error in ${error.process}`}>
      <h3>Process error: {error.cause}</h3>
      <p className="wo-error">{error.message}</p>
      <dl className="wo-facts">
        <dt>Process</dt><dd>{error.process}</dd>
        <dt>Step</dt><dd>{error.step ?? "–"}</dd>
        {error.edge !== null && <><dt>Edge</dt><dd>{error.edge}</dd></>}
        {error.trace !== null && (
          <><dt>Trace</dt><dd><code>{error.trace.uri}</code> (event {error.trace.seq})</dd></>
        )}
      </dl>
      <h4>Inputs</h4>
      <JsonView value={error.inputs} label="Error inputs" collapsed={1} />
      {error.partial_outputs !== null && (
        <>
          <h4>Partial outputs</h4>
          <JsonView value={error.partial_outputs} label="Partial outputs" collapsed={1} />
        </>
      )}
      {Object.keys(error.detail).length > 0 && <JsonView value={error.detail} label="Detail" />}
      {error.step_error !== null && <StepErrorView title="Step error" error={error.step_error} />}
      {error.handler_error !== null && <StepErrorView title="The error handler failed" error={error.handler_error} />}
      {error.workspace !== null && (
        <p>Workspace kept for inspection: <code>{error.workspace}</code></p>
      )}
    </section>
  );
}

function StepErrorView({ title, error }: { title: string; error: StepError }): ReactElement {
  return (
    <div className="wo-step-error">
      <h4>{title}: {error.cause}{error.type !== null && ` (${error.type})`}</h4>
      <p>{error.message}</p>
      {error.attempts > 1 && <p className="wo-muted">after {error.attempts} attempts</p>}
      {error.traceback !== null && <pre className="wo-traceback">{error.traceback}</pre>}
      {error.child !== null && <ProcessErrorView error={error.child} />}
    </div>
  );
}

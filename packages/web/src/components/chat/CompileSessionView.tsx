// Per-step decisions, inferred schemas, questions, events (`$DRAFTS/07 §9.7`). A proposal's correction card uses the
// step's declared interface when the job's process is the one open in the editor, else the compiler's inferred one.
import { useCallback, useContext, useSyncExternalStore, type ReactElement } from "react";
import { DesignContext } from "../../api/context";
import type { CompileDecision, CompileSession, CompileStep, Interface, Job, StepInfo } from "../../api/types";
import { stepSentence } from "../../model/schemaText";
import { Spinner } from "../common/Spinner";
import { ProposalCard } from "./ProposalCard";
import { QuestionCard } from "./QuestionCard";

export interface CompileSessionViewProps {
  job: Job;
  session: CompileSession;
}

export function decisionLabel(d: CompileDecision): string {
  if (d.split !== null) return `split → ${d.split.deterministic} + ${d.split.agentic}`;
  if (d.kind === "agentic") return ["agentic", d.tier, d.thinking].filter((p) => p !== null && p !== "").join(" · ");
  return d.kind;
}

const WORKING: readonly CompileStep["phase"][] = ["pending", "generating", "testing", "revising"];

function Phase({ step }: { step: CompileStep }): ReactElement {
  if (WORKING.includes(step.phase)) {
    return <span className="wc-phase" data-phase={step.phase}><Spinner label={step.phase} /> {step.phase}</span>;
  }
  const mark = step.phase === "done" ? "✓ done" : step.phase === "failed" ? "✕ failed" : "skipped";
  return <span className="wc-phase" data-phase={step.phase}>{mark}</span>;
}

function DecisionRow({ step }: { step: CompileStep }): ReactElement {
  const d = step.decision;
  return (
    <tr className="wc-decision" data-phase={step.phase}>
      <th scope="row">
        {step.step} <span className="wc-muted">{step.use}</span>
      </th>
      <td><Phase step={step} /></td>
      <td>
        {d === null
          ? <span className="wc-muted">{step.skipped_reason ?? "—"}</span>
          : (
            <>
              <span className="wc-badge" data-kind={d.split === null ? d.kind : "split"}>{decisionLabel(d)}</span>
              {d.split !== null && (
                <p className="wc-split">
                  Deterministic code passed {step.tests?.passed ?? 0} of {step.tests?.total ?? 0} examples; the rest go
                  to an agentic step via the error exit.
                </p>
              )}
              <details className="wc-why">
                <summary>Why</summary>
                <p>{d.reason}</p>
              </details>
            </>
          )}
      </td>
      <td>{step.tests === null ? "—" : `${step.tests.passed}/${step.tests.total}`}</td>
      <td>{step.attempts}</td>
    </tr>
  );
}

/** The editor's step info when `processId` is the open process (reactive), else null. */
function useOpenSteps(processId: string): Record<string, StepInfo> | null {
  const design = useContext(DesignContext);
  const subscribe = useCallback((fn: () => void) => design?.store.subscribe(fn) ?? (() => undefined), [design]);
  return useSyncExternalStore(subscribe, () => {
    const state = design?.store.get() ?? null;
    return state !== null && state.processId === processId ? state.steps : null;
  });
}

export function CompileSessionView({ job, session }: CompileSessionViewProps): ReactElement {
  const steps = useOpenSteps(job.process_id);
  const pending = session.questions.filter((q) => q.status === "pending");
  const answered = session.questions.filter((q) => q.status === "answered");
  const inferred = Object.entries(session.inferred_schemas);

  const ifaceOf = (step: string): Interface | null => {
    const declared = steps?.[step]?.interface ?? null;
    if (declared !== null && declared.source !== null) return declared;
    return session.inferred_schemas[step] ?? declared;
  };

  const card = (q: CompileSession["questions"][number]): ReactElement =>
    q.kind === "clarification"
      ? <QuestionCard key={q.id} jobId={job.id} question={q} />
      : <ProposalCard key={q.id} jobId={job.id} question={q} iface={ifaceOf(q.step)} />;

  return (
    <div className="wc-session">
      <table className="wc-decisions">
        <caption>Decisions per step</caption>
        <thead>
          <tr><th scope="col">Step</th><th scope="col">Phase</th><th scope="col">Decision</th><th scope="col">Tests</th><th scope="col">Attempts</th></tr>
        </thead>
        <tbody>{session.steps.map((s) => <DecisionRow key={s.step} step={s} />)}</tbody>
      </table>
      {inferred.length > 0 && (
        <section className="wc-inferred" aria-label="Inferred schemas">
          {inferred.map(([step, iface]) => (
            <p key={step}>
              <strong>{step}</strong>: {stepSentence(iface)} <span className="wc-muted">Inferred from examples</span>
            </p>
          ))}
        </section>
      )}
      {pending.length > 0 && (
        <section className="wc-questions" aria-label="Pending questions">
          <h4>Questions for you ({pending.length})</h4>
          {pending.map(card)}
        </section>
      )}
      {answered.length > 0 && (
        <details className="wc-questions">
          <summary>Answered questions ({answered.length})</summary>
          {answered.map(card)}
        </details>
      )}
      {session.events.length > 0 && (
        <details className="wc-events">
          <summary>Events ({session.events.length})</summary>
          <ol>
            {session.events.map((e, i) => (
              <li key={i}>
                <time dateTime={e.at}>{e.at.slice(11, 19)}</time>
                {e.step !== null && <span className="wc-chip">{e.step}</span>} {e.text}
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}

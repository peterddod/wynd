// Example proposal: [Confirm] [Correct…] [Not a real case] (`$DRAFTS/07 §9.7`). The proposal shows as a sentence and
// a read-only example card; [Correct…] opens an editable card prefilled with it and [Save correction] sends it.
import { useState, type ReactElement } from "react";
import type { ExampleProposalQuestion, Interface, Json } from "../../api/types";
import { exampleSentence } from "../../model/values";
import { ErrorBox } from "../common/ErrorBox";
import { ExampleCard } from "../examples/ExampleCard";
import { useAnswer } from "./QuestionCard";

export interface ProposalCardProps {
  jobId: string;
  question: ExampleProposalQuestion;
  iface: Interface | null;           // the step's interface (exits and field schemas for the correction card)
}

const DECISION_LABEL = { confirm: "Confirmed", correct: "Corrected", reject: "Not a real case" } as const;

/** The step's declared exits, the proposal's own exit and `error` (an example may expect the step to fail). */
function exitsOf(iface: Interface | null, proposed: string): string[] {
  return [...new Set([...(iface?.exits.map((e) => e.name) ?? []), proposed, "error"])];
}

export function ProposalCard({ jobId, question, iface }: ProposalCardProps): ReactElement {
  const { submit, busy, error } = useAnswer(jobId);
  const { inputs, outputs, exit } = question.proposed;
  const proposal: Json = { inputs, outputs, exit };
  const [draft, setDraft] = useState<Json | null>(null);    // non-null while correcting
  const exits = exitsOf(iface, exit);
  const answer = question.answer;

  const saveCorrection = async (example: Json): Promise<void> => {
    if (await submit({ question_id: question.id, decision: "correct", example })) setDraft(null);
  };

  return (
    <article className="wc-question wc-proposal" data-status={question.status} aria-label={`Proposal: ${question.text}`}>
      <p className="wc-question-text"><span className="wc-chip">{question.step}</span> {question.text}</p>
      {question.detail != null && <p className="wc-muted">{question.detail}</p>}
      <p className="wc-sentence">{exampleSentence(proposal, iface)}</p>
      <ExampleCard example={proposal} exits={exits} iface={iface} />
      {question.status === "answered" && answer !== null && (
        <p className="wc-answer">
          Answer: {DECISION_LABEL[answer.decision]}{answer.text !== null && answer.text !== "" ? ` (${answer.text})` : ""}
        </p>
      )}
      {question.status === "pending" && draft === null && (
        <div className="wc-actions">
          <button type="button" disabled={busy} onClick={() => void submit({ question_id: question.id, decision: "confirm" })}>
            Confirm
          </button>
          <button type="button" disabled={busy} onClick={() => setDraft(proposal)}>Correct…</button>
          <button type="button" disabled={busy} onClick={() => void submit({ question_id: question.id, decision: "reject" })}>
            Not a real case
          </button>
        </div>
      )}
      {question.status === "pending" && draft !== null && (
        <div className="wc-correction">
          <ExampleCard example={draft} exits={exits} iface={iface} onChange={setDraft} />
          <div className="wc-actions">
            <button type="button" disabled={busy} onClick={() => void saveCorrection(draft)}>Save correction</button>
            <button type="button" disabled={busy} onClick={() => setDraft(null)}>Cancel</button>
          </div>
        </div>
      )}
      {error !== null && <ErrorBox error={error} />}
    </article>
  );
}

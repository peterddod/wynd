// Clarification question: text + [Answer] -> {question_id, text} (`$DRAFTS/07 §9.7`). The returned job replaces the
// cached one; the controller resubmits the job once no question is pending.
import { useState, type ReactElement } from "react";
import { useApi } from "../../api/context";
import type { AnswerRequest, ClarificationQuestion } from "../../api/types";
import { invalidate, setQueryData } from "../../state/query";
import { ErrorBox } from "../common/ErrorBox";

export interface QuestionCardProps {
  jobId: string;
  question: ClarificationQuestion;
}

export interface Answering {
  submit(body: AnswerRequest): Promise<boolean>;   // true when the answer was accepted
  busy: boolean;
  error: Error | null;
}

/** Posts one answer and puts the returned job into the `job:<id>` cache (shared by both question cards). */
export function useAnswer(jobId: string): Answering {
  const api = useApi();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const submit = async (body: AnswerRequest): Promise<boolean> => {
    setBusy(true);
    setError(null);
    try {
      setQueryData(`job:${jobId}`, await api.jobs.answer(jobId, body));
      invalidate("chats");                           // the chat list's "needs answers (n)" badge
      return true;
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { submit, busy, error };
}

export function QuestionCard({ jobId, question }: QuestionCardProps): ReactElement {
  const { submit, busy, error } = useAnswer(jobId);
  const [text, setText] = useState("");
  const answered = question.status === "answered";

  return (
    <article className="wc-question" data-status={question.status} aria-label={`Question: ${question.text}`}>
      <p className="wc-question-text">
        {question.step !== null && <span className="wc-chip">{question.step}</span>} {question.text}
      </p>
      {question.detail != null && <p className="wc-muted">{question.detail}</p>}
      {answered
        ? <p className="wc-answer">Answer: {question.answer?.text ?? ""}</p>
        : (
          <form
            className="wc-answer-form"
            onSubmit={(e) => {
              e.preventDefault();
              if (text.trim() !== "") void submit({ question_id: question.id, text: text.trim() });
            }}
          >
            <label>
              Your answer
              <textarea
                rows={2}
                value={text}
                placeholder={question.default ?? undefined}
                disabled={busy}
                onChange={(e) => setText(e.target.value)}
              />
            </label>
            <button type="submit" disabled={busy || text.trim() === ""}>Answer</button>
          </form>
        )}
      {error !== null && <ErrorBox error={error} />}
    </article>
  );
}

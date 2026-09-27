// Clarification question: text + [Answer] -> {question_id, text} (`$DRAFTS/07 §9.7`). Stub from WEB-SCAFFOLD; WEB-CHAT implements it.
import type { ReactElement } from "react";
import type { ClarificationQuestion } from "../../api/types";

export interface QuestionCardProps {
  jobId: string;
  question: ClarificationQuestion;
}

export function QuestionCard(_props: QuestionCardProps): ReactElement | null {
  return null;
}

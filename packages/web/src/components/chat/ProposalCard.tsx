// Example proposal: [Confirm] [Correct…] [Not a real case] (`$DRAFTS/07 §9.7`). Stub from WEB-SCAFFOLD; WEB-CHAT implements it.
import type { ReactElement } from "react";
import type { ExampleProposalQuestion, Interface } from "../../api/types";

export interface ProposalCardProps {
  jobId: string;
  question: ExampleProposalQuestion;
  iface: Interface | null;           // the step's interface (exits and field schemas for the correction card)
}

export function ProposalCard(_props: ProposalCardProps): ReactElement | null {
  return null;
}

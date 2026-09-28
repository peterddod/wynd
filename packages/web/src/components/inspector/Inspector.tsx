// Inspector panel by URL `sel` (`$DRAFTS/07 §7.7`): nothing -> process overview, "s:<step>" -> step,
// "b:<e>:<b>" -> the edge with that branch expanded, "x:<exit>" -> process exit, "in" -> inputs. A selection that no
// longer exists shows the overview.
import type { ReactElement } from "react";
import { getIn } from "../../model/json";
import { useUrlState } from "../../state/url";
import { parseSel, useDesignState } from "../graph/designHooks";
import { EdgeInspector } from "./EdgeInspector";
import { InputsInspector } from "./InputsInspector";
import { ProcessOverview } from "./ProcessOverview";
import { StepInspector } from "./StepInspector";
import { TerminalInspector } from "./TerminalInspector";

export function Inspector(): ReactElement | null {
  const s = useDesignState();
  const [url] = useUrlState();
  if (s === null || s.parseError !== null) return null;
  const sel = parseSel(url.sel);
  let body: ReactElement = <ProcessOverview />;
  if (sel?.kind === "step" && getIn(s.process.doc, ["steps", sel.step]) !== undefined) body = <StepInspector key={sel.step} step={sel.step} />;
  if (sel?.kind === "branch" && getIn(s.process.doc, ["edges", sel.e]) !== undefined) {
    body = <EdgeInspector key={sel.e} edgeIndex={sel.e} branchIndex={sel.b} />;
  }
  if (sel?.kind === "exit") body = <TerminalInspector key={sel.exit} exit={sel.exit} />;
  if (sel?.kind === "inputs") body = <InputsInspector />;
  return <section className="wg-inspector" aria-label="Inspector">{body}</section>;
}

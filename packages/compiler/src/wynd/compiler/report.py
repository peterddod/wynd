"""The compile report (`JobRecord.report`, never committed) and the commit message (`$DRAFTS/05 §15`).

The pipeline fills `steps`, `process_changes`, `proto_changes`, `integration_tests`, `validation`, `warnings` and the
recording usage; the job handler fills the identity fields, `status`, `summary`, `questions`, compiler usage and
`error`. `render_commit_message` turns it into the squash commit's message.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from wynd.runtime.usage import Usage

ReportStatus = Literal["running", "awaiting_input", "succeeded", "failed"]
StepAction = Literal["compiled", "skipped", "awaiting", "failed"]


class StepUsage(BaseModel):
    compiler: Usage = Usage()           # the compiler's own LLM calls for this node
    recording: Usage = Usage()          # the step's own model calls while its tests recorded


class ReportStep(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    process: str
    node: str                           # node name in the process
    step: str | None = None             # package name, e.g. read_pdf
    package: str | None = None          # workspace-relative package dir
    action: StepAction = "compiled"
    reason: str = ""
    decision: dict[str, Any] | None = None       # {"kind", "rule", "why", "classification", "tier", "split"?}
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")   # {"inferred", "plain"}
    examples: dict[str, int] | None = None       # {"original", "proposed", "confirmed", "corrected", "rejected"}
    attempts: list[dict[str, Any]] = []
    tests: dict[str, int] | None = None          # {"passed", "failed"}
    tools: list[str] = []
    mcp: list[dict[str, Any]] = []
    effects: list[str] = []
    env: list[str] = []
    deps: dict[str, list[str]] | None = None     # {"declared", "locked"}
    cassette_bytes: int = 0
    usage: StepUsage = StepUsage()
    warnings: list[str] = []


class ReportUsage(BaseModel):
    total: Usage = Usage()              # compiler calls
    by_kind: dict[str, Usage] = {}      # call kind -> usage
    by_tier: dict[str, Usage] = {}      # tier -> usage
    recording: Usage = Usage()          # the steps' own model calls during record runs


class QuestionCounts(BaseModel):
    asked: int = 0
    answered_by_user: int = 0
    answered_by_accept_proposals: int = 0


class CompileReport(BaseModel):
    """Report JSON of `$DRAFTS/05 §15`."""
    model_config = ConfigDict(populate_by_name=True)

    report_version: Literal[1] = 1
    process: str = ""
    session: str = ""
    jobs: list[str] = []
    base_commit: str = ""
    commit: str | None = None
    branch: str | None = None
    status: ReportStatus = "running"
    summary: str = ""
    steps: list[ReportStep] = []
    process_changes: list[dict[str, Any]] = []   # {"type": "split"|"unsplit", "process", "node", "added_node", ...}
                                                 # | {"type": "edges_lock", "process", "branches"}
    proto_changes: list[dict[str, Any]] = []     # {"proto": <path>, "examples_added": n}
    integration_tests: dict[str, Any] | None = None   # {"passed", "failed", "cases"}
    validation: dict[str, list[Any]] = {"errors": [], "warnings": []}
    questions: QuestionCounts = QuestionCounts()
    usage: ReportUsage = ReportUsage()
    warnings: list[str] = []
    error: str | None = None

    def step_entry(self, process: str, node: str) -> ReportStep:
        """The entry of `node` in `process`, appended when missing."""
        for entry in self.steps:
            if entry.process == process and entry.node == node:
                return entry
        entry = ReportStep(process=process, node=node)
        self.steps.append(entry)
        return entry


def summarize(report: CompileReport) -> str:
    """One or two plain sentences: what was compiled (by kind) and skipped, then the test totals."""
    compiled = [s for s in report.steps if s.action == "compiled"]
    skipped = [s for s in report.steps if s.action == "skipped"]
    if not compiled:
        if report.status == "failed":
            return "Nothing was compiled."
        return "Nothing to compile: every step is unchanged."
    kinds: dict[str, int] = {}
    for s in compiled:
        kind = compiled_kind(s)
        kinds[kind] = kinds.get(kind, 0) + 1
    parts = [f"{n} {'split into deterministic + agentic' if kind == 'split' else kind}"
             for kind, n in sorted(kinds.items(), key=lambda item: _KIND_ORDER.index(item[0]))]
    text = f"Compiled {_count(len(compiled), 'step')}: {', '.join(parts)}"
    if skipped:
        text += f"; {len(skipped)} skipped (unchanged)"
    text += "."
    step_tests = sum((s.tests or {}).get("passed", 0) + (s.tests or {}).get("failed", 0) for s in compiled)
    step_failed = sum((s.tests or {}).get("failed", 0) for s in compiled)
    process_tests = report.integration_tests or {}
    total_process = process_tests.get("passed", 0) + process_tests.get("failed", 0)
    if step_tests or total_process:
        failed = step_failed + process_tests.get("failed", 0)
        counts = " and ".join(_count(n, noun) for n, noun in ((step_tests, "step test"),
                                                              (total_process, "process test")) if n)
        text += f" All {counts} pass." if not failed else f" {failed} of {counts} fail."
    return text


def render_commit_message(report: CompileReport) -> str:
    """Summary line, one line per step, process and proto changes, then the Wynd-Session/Process/Base trailers."""
    lines = [f"wynd compile: {report.process}", "", report.summary or summarize(report)]
    if report.steps:
        lines.append("")
        lines += [f"- {_step_line(s)}" for s in report.steps]
    changes = [_change_text(c) for c in report.process_changes]
    protos = [f"{_count(c.get('examples_added', 0), 'confirmed edge-case example')} added to {_stem(c['proto'])}"
              for c in report.proto_changes]
    if changes or protos:
        lines.append("")
    if changes:
        lines.append(f"Process changes: {'; '.join(changes)}.")
    if protos:
        lines.append(f"Proto-steps: {'; '.join(protos)}.")
    lines += ["", *trailers(report)]
    return "\n".join(lines) + "\n"


def render_wip_message(report: CompileReport) -> str:
    """The in-progress commit made when a pass stops at questions (`$DRAFTS/05 §5.2`)."""
    return "\n".join([f"wynd compile (in progress): {report.process}", "", *trailers(report)]) + "\n"


def trailers(report: CompileReport) -> list[str]:
    return [f"Wynd-Session: {report.session}", f"Wynd-Process: {report.process}", f"Wynd-Base: {report.base_commit}"]


_KIND_ORDER = ["deterministic", "agentic", "split", "shell", "process", "unknown"]


def compiled_kind(step: ReportStep) -> str:
    """deterministic, agentic, split, shell, process or unknown, from the step's decision."""
    decision = step.decision or {}
    if decision.get("split") or decision.get("kind") == "split":
        return "split"
    kind = decision.get("kind")
    return kind if kind in _KIND_ORDER else "unknown"


def _step_line(step: ReportStep) -> str:
    head = f"{step.node} ({step.step})" if step.step and step.step != step.node else step.node
    match step.action:
        case "skipped":
            return f"{head}: skipped ({step.reason or 'unchanged'})."
        case "awaiting":
            return f"{head}: awaiting input."
        case "failed":
            return f"{head}: failed. {step.reason}".rstrip()
    decision = step.decision or {}
    kind = compiled_kind(step)
    text = f"{head}: {kind}"
    if decision.get("rule") is not None:
        text += f", rule {decision['rule']}"
    if decision.get("tier") and kind in ("agentic", "split"):
        text += f" ({decision['tier']} tier)"
    text += "."
    why = decision.get("why") or decision.get("reason")
    return f"{text} {why}" if why else text


def _change_text(change: dict[str, Any]) -> str:
    match change.get("type"):
        case "split":
            edges = len(change.get("edges_added", []))
            return f"added node {change.get('added_node')} and {_count(edges, 'edge')} (split of {change.get('node')})"
        case "unsplit":
            removed = change.get("removed_node") or change.get("added_node")
            return f"removed node {removed} (split of {change.get('node')} undone)"
        case "edges_lock":
            branches = ", ".join(change.get("branches", [])) or "no agentic branches"
            return f"edges.lock.yaml of {change.get('process')} locks {branches}"
        case other:
            return f"{other} of {change.get('node')}"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _stem(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.removesuffix(".yaml")

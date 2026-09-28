"""CLI-M3 fake compile job handlers (`support.cli_m3_handlers:<function>` in runner handler tables) and a fake of
CTL-M3's `compile_view` (`session_dto`, `apply_answers`) over the compiler's session JSON shape (`$DRAFTS/05 §6.5`).

`ask_then_compile` asks two questions (an example proposal `upper.example1` and a clarification `count.clarify1`);
questions already answered by the submitted `answers`, or proposals with `accept_proposals`, are not asked (the
compiler's pre-answers). Once nothing is pending — first attempt or resumed — it compiles: commits
`processes/<pid>/COMPILED.md` and returns a succeeded outcome with a compile report. The submitted inputs are kept
in `artefacts["inputs"]`.
"""

from __future__ import annotations

import subprocess
from typing import Any

from wynd.process.jobs import JobContext, JobOutcome, JobUsage
from wynd.runtime.usage import Usage

COMPILED = "processes/{pid}/COMPILED.md"
ASKED_AT = "2026-09-22T21:16:02Z"
PROPOSAL = {
    "id": "upper.example1", "kind": "example_proposal", "step": "upper", "package": "upper",
    "text": "What should happen if the text is empty?",
    "detail": "Why: an empty message has nothing to upper-case.",
    "proposed": {"inputs": {"text": ""}, "exit": "done", "outputs": {"text": ""}},
    "expects": "decision", "default": "accept",
}
CLARIFICATION = {
    "id": "count.clarify1", "kind": "clarification", "step": "count", "package": "count",
    "text": "Do hyphenated words count as one word?", "detail": "", "proposed": None, "expects": "text",
    "default": None,
}
USAGE = JobUsage(input_tokens=812, output_tokens=64, calls=2, cost_usd=0.0011,
                 by={"fake/strong": Usage(input_tokens=812, output_tokens=64, calls=2, cost_usd=0.0011)})


def ask_then_compile(ctx: JobContext) -> JobOutcome:
    pid = ctx.inputs["process"]
    answers = ctx.inputs.get("answers") or {}
    session = ctx.session or _session(pid, ctx.job.id)
    for question in session["questions"]:
        if question["status"] != "pending":
            continue
        if question["id"] in answers:
            _answered(question, answers[question["id"]], "user")
        elif ctx.inputs.get("accept_proposals") and question["kind"] == "example_proposal":
            _answered(question, "accept", "accept_proposals")
    if any(q["status"] == "pending" for q in session["questions"]):
        (ctx.workspace / f"processes/{pid}/WIP.md").write_text("work in progress\n")
        wip = ctx.commit("wynd compile (in progress)", None)
        session = {**session, "state": "awaiting_input"}
        ctx.save_session(session)
        pending = [q for q in session["questions"] if q["status"] == "pending"]
        return JobOutcome(status="awaiting_input", commit=wip, session=session, questions=pending, usage=USAGE)
    return _compile(ctx, session)


def compile_fails(ctx: JobContext) -> JobOutcome:
    """Commits its partial work and fails: `count` failed its tests after the revisions."""
    pid = ctx.inputs["process"]
    (ctx.workspace / COMPILED.format(pid=pid)).write_text("partial\n")
    sha = ctx.commit("wynd compile (failed)", None)
    report = _report(pid, ctx.job.id, status="failed", summary="Compiled 1 step: 1 deterministic.")
    report["steps"][1] = {"process": pid, "node": "count", "step": "count", "action": "failed",
                          "reason": "tests still fail after 3 revisions", "tests": {"passed": 0, "failed": 1}}
    report["error"] = "tests failed after 3 revisions: count"
    session = {**_session(pid, ctx.job.id), "state": "failed", "questions": []}
    return JobOutcome(status="failed", commit=sha, report=report, session=session,
                      error="tests failed after 3 revisions: count", usage=USAGE)


def concurrent_edit(ctx: JobContext) -> JobOutcome:
    """Someone commits to the same file on the user's branch while the job runs: the branch is left for review."""
    pid = ctx.inputs["process"]
    rel = COMPILED.format(pid=pid)
    (ctx.workspace_root / rel).write_text("edited on main\n")
    for args in (["add", rel], ["commit", "-q", "-m", "concurrent edit"]):
        subprocess.run(["git", "-C", str(ctx.workspace_root), *args], check=True, capture_output=True)
    return _compile(ctx, _session(pid, ctx.job.id))


def dirty_checkout(ctx: JobContext) -> JobOutcome:
    """Leaves an untracked file in the user's checkout where the job's commit adds one."""
    pid = ctx.inputs["process"]
    (ctx.workspace_root / COMPILED.format(pid=pid)).write_text("local, uncommitted\n")
    return _compile(ctx, _session(pid, ctx.job.id))


def _compile(ctx: JobContext, session: dict[str, Any]) -> JobOutcome:
    pid = ctx.inputs["process"]
    answers = ctx.inputs.get("answers") or {}
    (ctx.workspace / COMPILED.format(pid=pid)).write_text(f"answers: {sorted(answers)}\n")
    sha = ctx.commit(f"wynd compile: {pid}", None)
    events = [*session["events"],
              {"seq": len(session["events"]) + 1, "at": ASKED_AT, "type": "decision", "process": pid,
               "step": "upper", "text": "upper: deterministic (rule 1). Upper-casing is a pure function.", "data": {}}]
    session = {**session, "state": "done", "events": events}
    return JobOutcome(status="succeeded", commit=sha, report=_report(pid, ctx.job.id), session=session,
                      artefacts={"inputs": ctx.inputs}, usage=USAGE)


def _session(pid: str, job_id: str) -> dict[str, Any]:
    questions = [{**q, "process": pid, "fingerprint": f"sha256:{q['id']}", "status": "pending", "answer": None,
                  "answered_by": None, "asked_at": ASKED_AT, "asked_in_job": job_id}
                 for q in (PROPOSAL, CLARIFICATION)]
    events = [{"seq": 1, "at": ASKED_AT, "type": "note", "process": pid, "step": None,
               "text": f"Compiling {pid}: 2 steps, 2 with changed proto-steps.", "data": {}}]
    events += [{"seq": i + 2, "at": ASKED_AT, "type": "question", "process": pid, "step": q["step"],
                "text": q["text"], "data": {}} for i, q in enumerate(questions)]
    return {"version": 1, "id": job_id, "process": pid, "state": "running", "questions": questions,
            "events": events, "steps": []}


def _answered(question: dict[str, Any], text: str, by: str) -> None:
    question.update(status="answered", answer={"text": text}, answered_by=by)


def _report(pid: str, job_id: str, *, status: str = "succeeded",
            summary: str = "Compiled 2 steps: 2 deterministic. All 3 step tests pass.") -> dict[str, Any]:
    return {
        "report_version": 1, "process": pid, "session": job_id, "status": status, "summary": summary,
        "steps": [
            {"process": pid, "node": "upper", "step": "upper", "action": "compiled",
             "decision": {"kind": "deterministic", "rule": 1, "why": "Upper-casing is a pure function.",
                          "tier": None}, "tests": {"passed": 2, "failed": 0}},
            {"process": pid, "node": "count", "step": "count", "action": "compiled",
             "decision": {"kind": "agentic", "rule": 2, "why": "Word boundaries need judgement.", "tier": "cheap",
                          "split": None}, "tests": {"passed": 1, "failed": 0}},
            {"process": "child", "node": "tag", "step": "tag", "action": "skipped",
             "reason": "proto-step unchanged"},
        ],
        "integration_tests": {"passed": 2, "failed": 0},
        "warnings": ["cassettes of count are 6.1 MB (above 5 MB)"],
    }


# --- fake CTL-M3 compile_view -------------------------------------------------------------------------------------

def session_dto(session: dict | None):
    """The web `CompileSession` projection of a session JSON (questions, events, state)."""
    from wynd.controller.api.models_web import CompileSession

    if session is None:
        return None
    state = {"awaiting_input": "awaiting_input", "done": "done", "failed": "failed"}.get(session.get("state"),
                                                                                          "compiling")
    keep = ("id", "kind", "step", "text", "proposed", "status", "asked_at", "detail", "default")
    questions = [{k: q[k] for k in keep if q.get(k) is not None} for q in session.get("questions", [])]
    events = [{k: e[k] for k in ("at", "type", "step", "text")} for e in session.get("events", [])]
    return CompileSession.model_validate({"state": state, "questions": questions, "events": events})


class FakeCompileView:
    """`apply_answers` marks the answered questions (`calls` records `(job id, answers)`); ready when none is
    pending."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def apply_answers(self, job, answers) -> tuple[dict, bool]:
        self.calls.append((job.id, dict(answers)))
        questions = [dict(q) for q in job.session["questions"]]
        for question in questions:
            if question["status"] == "pending" and question["id"] in answers:
                _answered(question, answers[question["id"]], "user")
        ready = not any(q["status"] == "pending" for q in questions)
        return {**job.session, "questions": questions, "state": "ready" if ready else "awaiting_input"}, ready

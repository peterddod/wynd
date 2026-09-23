"""The compile report (`JobRecord.report`, never committed) and the commit message (`$DRAFTS/05 §15`)."""

from __future__ import annotations


class CompileReport:
    """Report JSON of `$DRAFTS/05 §15`: status, summary, per-step decisions and tests, process and proto changes,
    integration tests, validation, questions, usage, warnings, error. Stub: fields land with CMP-A."""


def render_commit_message(report: CompileReport) -> str:
    """Summary line, one line per step, process and proto changes, then the Wynd-Session/Process/Base trailers."""
    raise NotImplementedError("PLAN §7")

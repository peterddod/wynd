"""Compile-session view (PLAN §8.1 compile_view row, §3.18 "Answering", §3.21 amendment 9; `$DRAFTS/06 §7.8`).
Stub; CTL-M3.

`session_dto` projects the compiler's `SessionData` JSON onto the web `CompileSession`; `answer_text` maps a web
answer to answer text (confirm -> "accept", reject -> "reject", correct+example -> `json.dumps(example)`,
text -> text); `apply_answers` uses `wynd.compiler.CompileSession.from_json(...).answer(...)`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.controller.api.models_web import AnswerRequest, CompileSession
    from wynd.process.jobs import JobRecord


def session_dto(session: dict | None) -> CompileSession | None:
    raise NotImplementedError("PLAN §8.1")


def answer_text(question: dict, answer: AnswerRequest) -> str:
    raise NotImplementedError("PLAN §8.1")


def apply_answers(job: JobRecord, answers: Sequence[AnswerRequest] | Mapping[str, str]) -> tuple[dict, bool]:
    """-> (the new session JSON, whether it is ready to resume)."""
    raise NotImplementedError("PLAN §8.1")

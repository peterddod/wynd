"""Test doubles for CMP-B's tests: the session surface the pipeline uses, and scripted attempt results."""

from __future__ import annotations

from types import SimpleNamespace


class FakeSession:
    """`ask` / `emit` / `data.questions`, answering from a {question id: Answer} mapping."""

    def __init__(self, answers: dict | None = None):
        self.answers = dict(answers or {})
        self.data = SimpleNamespace(questions=[])
        self.events: list[tuple[str, str]] = []

    def ask(self, q):
        answer = self.answers.get(q.id)
        self.data.questions.append(q.model_copy(update={"status": "answered" if answer else "pending",
                                                        "answer": answer}))
        return answer

    def emit(self, type, text, *, process, step, data=None):
        self.events.append((type, text))

    def asked(self) -> list[str]:
        return [q.id for q in self.data.questions]


def attempt_result(cases: dict[int, str], *, n: int = 1, kind: str = "deterministic", tier: str | None = None):
    """An AttemptResult whose counts follow from `cases` (example number -> handled|deferred|wrong)."""
    from wynd.compiler.attempts import AttemptResult

    handled = sum(1 for c in cases.values() if c == "handled")
    deferred = sum(1 for c in cases.values() if c == "deferred")
    wrong = sum(1 for c in cases.values() if c == "wrong")
    return AttemptResult(n=n, kind=kind, tier=tier, cases=dict(cases), failures=[], static_errors=[],
                         all_passed=handled == len(cases), handled=handled, deferred=deferred, wrong=wrong,
                         cassettes=None, events_file=None, duration_ms=1)

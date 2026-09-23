"""Diagnostics: the one finding type for spec, process, controller, cli and web (PLAN §3.2; $DRAFTS/01 §4.2)."""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

Severity = Literal["error", "warning", "info"]
Loc = tuple[str | int, ...]


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    code: str  # stable; tests assert codes, not prose
    message: str
    file: str | None = None  # workspace-relative
    line: int | None = None  # 1-based
    column: int | None = None  # 1-based
    loc: Loc = ()  # document path, e.g. ("edges", 3, "to", 0, "with", "dest")
    process: str | None = None  # process id the diagnostic belongs to
    span: tuple[int, int] | None = None  # 0-based [start, end) character offsets inside an expression
    snippet: str | None = None

    def format(self) -> str:
        """`{file}:{line}:{column}: {severity}[{code}] {loc}: {message}`, absent parts dropped; snippet lines follow
        indented four spaces."""
        position = ":".join(str(part) for part in (self.file, self.line, self.column) if part is not None)
        head = f"{self.severity}[{self.code}]"
        if self.loc:
            head = f"{head} {format_loc(self.loc)}"
        text = f"{head}: {self.message}"
        if position:
            text = f"{position}: {text}"
        if self.snippet:
            text += "".join(f"\n    {line}" for line in self.snippet.splitlines())
        return text

    def to_json(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "loc": list(self.loc),
            "process": self.process,
            "span": list(self.span) if self.span is not None else None,
            "snippet": self.snippet,
        }


class SpecError(ValueError):
    """Raised by loaders; str() is the formatted diagnostics, one per line."""

    def __init__(self, diagnostics: Iterable[Diagnostic]):
        self.diagnostics: list[Diagnostic] = list(diagnostics)
        super().__init__("\n".join(d.format() for d in self.diagnostics))


def format_loc(loc: Loc) -> str:
    """("edges", 3, "to", 0, "with", "dest") -> "edges[3].to[0].with.dest"."""
    text = ""
    for part in loc:
        match part:
            case int():
                text += f"[{part}]"
            case _ if text:
                text += f".{part}"
            case _:
                text = str(part)
    return text

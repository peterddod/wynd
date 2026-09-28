"""Expression errors (PLAN §3.5; $DRAFTS/01 §7.7–§7.8)."""

from typing import Any


class ExprError(ValueError):
    """Base of every expression error."""


class ExprSyntaxError(ExprError):
    """The expression does not parse; line/column are 1-based within the expression text, `span` the 0-based
    [start, end) offsets of the offending token (empty at the end of the text)."""

    def __init__(self, text: str, line: int, column: int, message: str, span: tuple[int, int] | None = None):
        self.text = text
        self.line = line
        self.column = column
        self.message = message
        self.span = span
        super().__init__(f"{line}:{column}: {message}")


class EvalError(ExprError):
    """Evaluation failed; `node` locates the failing sub-expression inside `text`."""

    def __init__(self, message: str, node: Any = None, text: str | None = None):
        self.message = message
        self.node = node
        self.text = text
        position = f"{node.line}:{node.column}: " if node is not None else ""
        super().__init__(f"{position}{message}")

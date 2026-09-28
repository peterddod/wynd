"""Shared fixtures of the spec document tests (SPEC-CORE). `tests/expr/` has its own conftest.

`expr_double` replaces the expression parser and `check_expression` (SPEC-EXPR) with a small deterministic double, so
these tests pin how documents locate expression findings, not the expression language itself. It is opt-in.
"""

import pytest

from wynd.spec.errors import Diagnostic
from wynd.spec.expr.errors import ExprSyntaxError

# Marker token -> the parser message the contract gives for it ($DRAFTS/01 §7.8).
SYNTAX_MARKERS = {"elze": "unexpected 'elze'; expected an operator, 'elif' or 'else'"}


def _position(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    return line, offset - (text.rfind("\n", 0, offset) + 1) + 1


def double_parse(text: str) -> object:
    for marker, message in SYNTAX_MARKERS.items():
        offset = text.find(marker)
        if offset >= 0:
            line, column = _position(text, offset)
            raise ExprSyntaxError(text, line, column, message, (offset, offset + len(marker)))
    return object()


def double_check_expression(text: str) -> list[Diagnostic]:
    try:
        double_parse(text)
    except ExprSyntaxError as err:
        return [Diagnostic("error", "E-EXPR-SYNTAX", err.message, line=err.line, column=err.column, span=err.span)]
    offset = text.find("nofunc(")
    if offset < 0:
        return []
    line, column = _position(text, offset)
    return [Diagnostic("error", "E-EXPR-FUNC", "unknown function 'nofunc'", line=line, column=column,
                       span=(offset, offset + 6))]


@pytest.fixture
def expr_double(monkeypatch):
    from wynd.spec.expr import analysis, evaluator

    monkeypatch.setattr(evaluator, "parse", double_parse)
    monkeypatch.setattr(analysis, "check_expression", double_check_expression)

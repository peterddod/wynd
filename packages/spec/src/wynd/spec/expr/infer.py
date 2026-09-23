"""Type inference for expressions and assignability of JSON Schemas (M3; PLAN §4.1; $DRAFTS/01 §7.10)."""

from typing import Literal

from wynd.spec.errors import Diagnostic
from wynd.spec.expr.analysis import TypeEnv


def infer_type(expr: str, env: TypeEnv) -> tuple[dict, list[Diagnostic]]:
    """Normalised JSON Schema of the expression's value ({} = unknown) and E-TYPE-OP / W-TYPE-NULL findings."""
    raise NotImplementedError("PLAN §4.1")


def check_assignable(src: dict, dst: dict) -> tuple[Literal["ok", "warning", "error"], str]:
    raise NotImplementedError("PLAN §4.1")

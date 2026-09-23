"""wynd.spec.expr: the edge expression language (grammar, evaluator, scope, static analysis, type inference).

Public names are re-exported lazily: a leaf module is imported on first access.
"""

import importlib

_MODULES = {
    ".errors": ("ExprError", "ExprSyntaxError", "EvalError"),
    ".scope": ("StepState", "EdgeState", "Scope", "new_scope", "utc_now"),
    ".evaluator": (
        "parse", "evaluate", "evaluate_condition", "evaluate_value", "evaluate_with", "evaluate_limit", "truthy",
        "strict_eq", "BUILTINS",
    ),
    ".analysis": (
        "Ref", "references", "value_references", "env_names", "check_expression", "StepView", "TypeEnv",
        "check_references",
    ),
    ".infer": ("infer_type", "check_assignable"),
}
_EXPORTS = {name: module for module, names in _MODULES.items() for name in names}
__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})

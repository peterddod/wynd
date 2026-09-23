"""Every code the validator emits is in a code table, and every validator code has a golden case (PLAN §3.2;
`$DRAFTS/04 §18.3` test_codes)."""

import ast
import json
import re
from pathlib import Path

import wynd.process.validation as validation_package
import wynd.runtime.lint as lint_module
from wynd.process.errors import CODES
from wynd.spec.errors import CODES as SPEC_CODES

VALIDATION = Path(validation_package.__file__).parent
MODULES = ["__init__", "structure", "reach", "cycles", "bindings", "dataflow", "exprcheck", "rules"]
GOLDEN = Path(__file__).parent / "validator" / "fixtures" / "cases"
CODE = re.compile(r"^(?:[EWIL]\d{3}|[EW]-[A-Z][A-Z-]*)$")
LINT_CODES = {f"L00{n}" for n in range(1, 6)}
VALIDATOR_CODES = {
    "E128", "W128", "W129", "I130", "E204", "E208", "E209", "E210", "E211", "E212", "E215", "E219", "E223", "I201",
    "W202", "W203", "W204", "W205", "W206", "W207",
}
REFERENCE_CODES = {"E-REF-STEP", "E-REF-UNRUN", "E-REF-FIELD", "E-REF-EXIT", "E-REF-INPUT", "E-REF-EDGE"}


def code_literals(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and CODE.match(node.value)
    }


def golden_codes() -> set[str]:
    return {d["code"] for case in GOLDEN.iterdir() if case.is_dir()
            for d in json.loads((case / "expected.json").read_text())["diagnostics"]}


def test_every_code_in_the_validator_is_in_a_code_table():
    found = set().union(*(code_literals(VALIDATION / f"{name}.py") for name in MODULES))
    assert found <= set(CODES) | SPEC_CODES | LINT_CODES
    assert found & set(CODES) == VALIDATOR_CODES
    assert REFERENCE_CODES <= found


def test_the_process_code_table_holds_the_validator_codes():
    assert VALIDATOR_CODES <= set(CODES)
    assert REFERENCE_CODES <= SPEC_CODES


def test_runtime_lint_codes_are_the_l_namespace():
    assert code_literals(Path(lint_module.__file__)) == LINT_CODES


def test_every_validator_code_has_a_golden_case():
    codes = golden_codes()
    assert VALIDATOR_CODES <= codes
    assert REFERENCE_CODES <= codes
    assert LINT_CODES <= codes
    assert codes <= set(CODES) | SPEC_CODES | LINT_CODES

"""wynd.spec is the dependency leaf (PLAN §2, §4.3): it imports no other wynd package and only pydantic, pyyaml and
lark beyond the standard library. Run in isolation too: `uv run --isolated --package wynd-spec --with pytest pytest
packages/spec/tests/test_leaf.py`."""

import ast
import importlib
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
PACKAGE = SRC / "wynd" / "spec"
ALLOWED_THIRD_PARTY = {"pydantic", "pydantic_core", "yaml", "lark", "typing_extensions"}
MODULES = sorted(
    ".".join(path.relative_to(SRC).with_suffix("").parts).removesuffix(".__init__")
    for path in PACKAGE.rglob("*.py")
)


def imported_modules(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=names):
                found += [(alias.name, node.lineno) for alias in names]
            case ast.ImportFrom(module=module, level=0) if module:
                found.append((module, node.lineno))
    return found


@pytest.mark.parametrize("path", sorted(PACKAGE.rglob("*.py")), ids=lambda p: str(p.relative_to(PACKAGE)))
def test_imports_stay_inside_the_leaf(path):
    bad = []
    for module, line in imported_modules(path):
        top = module.split(".")[0]
        if top == "wynd":
            if module != "wynd.spec" and not module.startswith("wynd.spec."):
                bad.append(f"{line}: {module}")
        elif top not in sys.stdlib_module_names and top not in ALLOWED_THIRD_PARTY:
            bad.append(f"{line}: {module}")
    assert bad == []


def test_no_namespace_init():
    assert not (SRC / "wynd" / "__init__.py").exists()


def test_the_module_list_is_complete():
    assert "wynd.spec.process_doc" in MODULES and "wynd.spec.expr.evaluator" in MODULES
    assert len(MODULES) >= 25


@pytest.mark.parametrize("module", MODULES)
def test_every_module_imports(module):
    importlib.import_module(module)

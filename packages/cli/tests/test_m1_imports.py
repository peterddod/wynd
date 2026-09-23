"""The CLI import rule (PLAN §3.22, §9; `$DRAFTS/06 §2.2`): every module under `wynd/cli` imports only the standard
library, `wynd.cli`, `wynd.controller`, `typer` and `yaml` — never `wynd.process`, `wynd.runtime`, `wynd.compiler`,
`wynd.spec` — and never shells out (no `subprocess`: Docker is reached only through the controller)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import wynd.cli

CLI_SRC = Path(wynd.cli.__file__).parent
ALLOWED_PACKAGES = {"typer", "yaml"}
ALLOWED_WYND = ("wynd.cli", "wynd.controller")
FORBIDDEN_STDLIB = {"subprocess"}


def imported_modules(path: Path) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        match node:
            case ast.Import(names=names):
                found += [(node.lineno, alias.name) for alias in names]
            case ast.ImportFrom(level=0, module=str(module)):
                found.append((node.lineno, module))
            case ast.ImportFrom(level=level) if level > 0:
                found.append((node.lineno, "." * level + (node.module or "")))
    return found


def violations(module: str) -> bool:
    top = module.split(".")[0]
    if module.startswith("."):
        return True                                 # relative imports hide what is imported
    if top == "wynd":
        return not any(module == allowed or module.startswith(allowed + ".") for allowed in ALLOWED_WYND)
    if top in FORBIDDEN_STDLIB:
        return True
    return top not in sys.stdlib_module_names and top not in ALLOWED_PACKAGES and top != "__future__"


def test_the_cli_sources_exist():
    files = sorted(CLI_SRC.rglob("*.py"))
    assert CLI_SRC / "main.py" in files and CLI_SRC / "commands" / "run.py" in files


def test_cli_imports_only_the_controller_typer_and_yaml():
    bad = [f"{path.relative_to(CLI_SRC)}:{line}: {module}"
           for path in sorted(CLI_SRC.rglob("*.py"))
           for line, module in imported_modules(path) if violations(module)]
    assert bad == []


def test_the_rule_rejects_what_it_should(tmp_path):
    source = tmp_path / "bad.py"
    source.write_text(
        "import subprocess\n"
        "from wynd.process.git import closure_head\n"
        "import wynd.runtime\n"
        "from wynd.spec import errors\n"
        "from . import output\n"
        "import pydantic\n"
        "from wynd.controllerx import y\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    from wynd.compiler import session\n"
        "from wynd.controller.models import Run\n"
        "import typer, yaml, json\n"
    )
    flagged = [module for _, module in imported_modules(source) if violations(module)]
    assert flagged == ["subprocess", "wynd.process.git", "wynd.runtime", "wynd.spec", ".", "pydantic",
                       "wynd.controllerx", "wynd.compiler"]

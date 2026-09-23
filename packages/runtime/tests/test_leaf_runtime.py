"""`wynd.runtime` is the in-image leaf above `wynd.spec` (PLAN §2, §5.7): it imports no wynd.process / compiler /
controller / cli / kube and no third-party package other than pydantic (with its core and typing_extensions), yaml
and lark; `claude_agent_sdk` only inside function bodies of `providers/claude_code.py`."""

import ast
import subprocess
import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "src" / "wynd" / "runtime"
THIRD_PARTY = {"pydantic", "pydantic_core", "typing_extensions", "yaml", "lark"}
SDK = "claude_agent_sdk"
SDK_MODULE = RUNTIME / "providers" / "claude_code.py"
FORBIDDEN_WYND = ("wynd.process", "wynd.compiler", "wynd.controller", "wynd.cli", "wynd.kube")


def _imports(node: ast.AST, in_function: bool = False):
    """(absolute module, line, inside a function body) for every import statement."""
    for child in ast.iter_child_nodes(node):
        match child:
            case ast.Import():
                for alias in child.names:
                    yield alias.name, child.lineno, in_function
            case ast.ImportFrom(level=0, module=str(module)):
                yield module, child.lineno, in_function
        inside = in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        yield from _imports(child, inside)


def _problem(module: str, path: Path, in_function: bool) -> bool:
    top = module.partition(".")[0]
    if top == "wynd":
        return not (module in ("wynd.spec", "wynd.runtime") or module.startswith(("wynd.spec.", "wynd.runtime.")))
    if top in sys.stdlib_module_names or top in THIRD_PARTY:
        return False
    return not (top == SDK and path == SDK_MODULE and in_function)


def _sources() -> list[Path]:
    return sorted(RUNTIME.rglob("*.py"))


def test_runtime_sources_import_only_the_image_set():
    problems = [
        f"{path.relative_to(RUNTIME)}:{line}: {module}"
        for path in _sources()
        for module, line, in_function in _imports(ast.parse(path.read_text(encoding="utf-8"), str(path)))
        if _problem(module, path, in_function)
    ]
    assert problems == []


def test_the_rule_catches_violations():
    tree = ast.parse("import wynd.process\nfrom httpx import Client\nimport claude_agent_sdk\n"
                     "def f():\n    import claude_agent_sdk\n")
    found = [(m, _problem(m, SDK_MODULE, f)) for m, _, f in _imports(tree)]
    assert found == [("wynd.process", True), ("httpx", True), ("claude_agent_sdk", True), ("claude_agent_sdk", False)]
    assert _problem("claude_agent_sdk", RUNTIME / "agentic" / "loop.py", True)


def test_importing_every_runtime_module_loads_no_upper_package():
    modules = [
        ".".join(("wynd", "runtime", *path.relative_to(RUNTIME).with_suffix("").parts)).removesuffix(".__init__")
        for path in _sources()
        if path.name != "__main__.py"
    ]
    code = (
        "import importlib, sys\n"
        f"for name in {modules!r}:\n    importlib.import_module(name)\n"
        f"print(sorted(m for m in sys.modules if m.startswith({FORBIDDEN_WYND!r}) or m.partition('.')[0] == {SDK!r}))"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"

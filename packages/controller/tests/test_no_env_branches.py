"""No environment branches in the controller (PLAN §0 code style, SPEC §15; `$DRAFTS/06 §11.3`).

Backends and settings are selected from the environment in exactly four modules: `controller.py` (backend
selection), `envfile.py` (`.env` loading), `api/app.py` (API settings) and `chat/engine.py` (chat provider/tier).
Everywhere else the process environment is only ever passed on whole (`dict(os.environ)`, `{**os.environ}`), never
consulted for one variable, and nothing branches on the platform.
"""

from __future__ import annotations

import ast
from pathlib import Path

import wynd.controller

SOURCES = Path(wynd.controller.__file__).parent
ALLOWED = {"controller.py", "envfile.py", "api/app.py", "chat/engine.py"}
LOOKUPS = {"get", "setdefault", "pop", "__getitem__", "__contains__"}


def _is_os_environ(node: ast.AST) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ" and isinstance(node.value, ast.Name)
            and node.value.id == "os")


def findings(source: str) -> list[str]:
    """`line: what` for every single-variable environment read and every platform check in `source`."""
    found = []
    for node in ast.walk(ast.parse(source)):
        match node:
            case ast.Attribute(attr=attr, value=value) if attr in LOOKUPS and _is_os_environ(value):
                found.append(f"{node.lineno}: os.environ.{attr}")
            case ast.Subscript(value=value) if _is_os_environ(value):
                found.append(f"{node.lineno}: os.environ[...]")
            case ast.Compare(ops=ops, comparators=comparators) if any(
                    isinstance(op, ast.In | ast.NotIn) for op in ops) and any(map(_is_os_environ, comparators)):
                found.append(f"{node.lineno}: ... in os.environ")
            case ast.Attribute(attr="getenv" | "putenv", value=ast.Name(id="os")):
                found.append(f"{node.lineno}: os.{node.attr}")
            case ast.ImportFrom(module="os", names=names) if any(a.name in ("environ", "getenv") for a in names):
                found.append(f"{node.lineno}: from os import environ/getenv")
            case ast.Attribute(attr="platform", value=ast.Name(id="sys")):
                found.append(f"{node.lineno}: sys.platform")
            case ast.Import(names=names) if any(a.name == "platform" for a in names):
                found.append(f"{node.lineno}: import platform")
            case ast.ImportFrom(module="platform"):
                found.append(f"{node.lineno}: from platform import ...")
    return sorted(found, key=lambda text: int(text.partition(":")[0]))


def test_the_checker_catches_every_form():
    source = """
import os, sys, platform
from os import getenv
a = os.environ.get("WYND_X")
b = os.environ["WYND_X"]
c = "WYND_X" in os.environ
d = os.getenv("WYND_X")
if sys.platform == "darwin":
    pass
"""
    assert findings(source) == ["2: import platform", "3: from os import environ/getenv", "4: os.environ.get",
                                "5: os.environ[...]", "6: ... in os.environ", "7: os.getenv", "8: sys.platform"]
    assert findings("import os\nenv = {**os.environ, 'A': '1'}\nsnapshot = dict(os.environ)\n") == []


def test_only_the_selection_modules_read_the_environment():
    offenders = {}
    for path in sorted(SOURCES.rglob("*.py")):
        rel = path.relative_to(SOURCES).as_posix()
        if rel in ALLOWED:
            continue
        found = findings(path.read_text(encoding="utf-8"))
        if found:
            offenders[rel] = found
    assert offenders == {}, "environment reads outside the selection modules (use ctx.env / EnvService)"
    assert {"controller.py", "envfile.py"} <= {p.relative_to(SOURCES).as_posix() for p in SOURCES.rglob("*.py")}

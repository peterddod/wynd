"""Static checks and extraction on generated step modules (`$DRAFTS/05 §9.7`, PLAN §7 item 11).

`wynd.runtime.lint.check_step_module` runs first and every `L` finding is an error; each error is fed back into a
revision like the compiler-specific ones.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from wynd.spec.context import parse_context_entry

if TYPE_CHECKING:
    from wynd.compiler.tools import ToolCatalog
    from wynd.spec.lockfiles import Effect

STEP_BASES = frozenset({"Step", "DeterministicStep", "AgenticStep", "ShellStep", "ProcessStep"})
_ENV_FUNCTIONS = frozenset({"env", "getenv"})


@dataclass
class ToolMethodInfo:
    """One `@tool(...)` method: its decorator arguments."""
    name: str
    effects: list[Effect]
    idempotent: bool
    env: list[str]


@dataclass
class StaticReport:
    errors: list[str]
    env_vars: set[str]
    tools: list[str]                          # names in `tools = [...]`
    mcp: list[tuple[str, list[str]]]          # McpServer("x", allow=[...]) literals
    context: list[str]
    tool_methods: list[ToolMethodInfo]
    shell_allow: list[str] = field(default_factory=list)   # shell.allow("x", ...) in `tools`


def lint_errors(source: str, path: str) -> list[str]:
    """`wynd.runtime.lint` findings as revision text (all `L` codes are errors)."""
    from wynd.runtime.lint import check_step_module

    try:
        issues = check_step_module(source, path)
    except SyntaxError as err:
        return [f"{path}: the module does not parse: line {err.lineno}: {err.msg}"]
    return [f"{i.path}:{i.line}:{i.col}: {i.code} {i.message}" for i in issues]


def check(source: str, *, class_name: str, base: Literal["DeterministicStep", "AgenticStep", "ShellStep"],
          catalog: ToolCatalog, upstream_nodes: list[str] | None, effects: Sequence[str] | None = None
          ) -> StaticReport:
    """Class shape, agentic attribute literals (`tools`, `mcp`, `context`, `run` body `...`), unknown tools, servers
    and context entries (`upstream_nodes` None skips the upstream check), and subprocess use without the `shell` effect
    (checked only when `effects` is given).
    Extraction: env vars, the attribute literals and the `@tool` methods."""
    report = StaticReport(errors=[], env_vars=set(), tools=[], mcp=[], context=[], tool_methods=[])
    try:
        tree = ast.parse(source)
    except SyntaxError as err:
        report.errors.append(f"the module does not parse: line {err.lineno}: {err.msg}")
        return report
    report.env_vars = _env_vars(tree)
    step = _step_class(tree, class_name, base, report.errors)
    if step is not None:
        report.tool_methods = _tool_methods(step, report)
        if base == "AgenticStep":
            _agentic(step, report, catalog, upstream_nodes)
    if base != "ShellStep" and effects is not None and "shell" not in effects and _runs_subprocess(tree):
        report.errors.append("the module runs subprocesses (subprocess / os.system) but does not declare the "
                             "'shell' effect")
    for method in report.tool_methods:
        report.env_vars.update(method.env)
    return report


def _step_class(tree: ast.Module, class_name: str, base: str, errors: list[str]) -> ast.ClassDef | None:
    steps = [n for n in tree.body if isinstance(n, ast.ClassDef) and _bases(n) & STEP_BASES]
    if not steps:
        errors.append(f"the module defines no step class; expected exactly one: class {class_name}({base})")
        return None
    if len(steps) > 1:
        names = ", ".join(n.name for n in steps)
        errors.append(f"the module defines {len(steps)} step classes ({names}); a step module has exactly one")
    step = next((n for n in steps if n.name == class_name), None)
    if step is None:
        errors.append(f"the step class must be named {class_name} (found {steps[0].name})")
        return None
    if base not in _bases(step):
        errors.append(f"{class_name} must derive from {base} (bases: {', '.join(sorted(_bases(step))) or 'none'})")
    return step


def _bases(node: ast.ClassDef) -> set[str]:
    out = set()
    for b in node.bases:
        match b:
            case ast.Name(id=name) | ast.Attribute(attr=name):
                out.add(name)
    return out


def _agentic(step: ast.ClassDef, report: StaticReport, catalog: ToolCatalog, upstream: list[str] | None) -> None:
    for stmt in step.body:
        match stmt:
            case ast.Assign(targets=[ast.Name(id="tools")], value=value):
                _tools(value, report, catalog)
            case ast.Assign(targets=[ast.Name(id="mcp")], value=value):
                _mcp(value, report, catalog)
            case ast.Assign(targets=[ast.Name(id="context")], value=value):
                _context(value, report, upstream)
            case ast.FunctionDef(name="run", body=body):
                if not (len(body) == 1 and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant) and body[0].value.value is Ellipsis):
                    report.errors.append("an AgenticStep's run body must be exactly `...` (the model runs it)")


def _tools(value: ast.expr, report: StaticReport, catalog: ToolCatalog) -> None:
    if not isinstance(value, ast.List):
        report.errors.append("tools must be a literal list of builtin tool names")
        return
    for item in value.elts:
        match item:
            case ast.Name(id="shell"):
                report.errors.append('the shell builtin needs an allowlist of executables: '
                                     'shell.allow("<executable>", ...)')
            case ast.Name(id=name):
                report.tools.append(name)
                if catalog.builtin(name) is None:
                    known = ", ".join(b.name for b in catalog.builtins)
                    report.errors.append(f"unknown builtin tool {name!r} (builtins: {known})")
            case ast.Call(func=ast.Attribute(value=ast.Name(id="shell"), attr="allow"), args=args, keywords=[]) if (
                    args and all(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in args)):
                report.tools.append("shell")
                report.shell_allow = [a.value for a in args]
            case _:
                report.errors.append(f"tools entries must be builtin tool names or shell.allow(\"...\") "
                                     f"(got {ast.unparse(item)})")


def _mcp(value: ast.expr, report: StaticReport, catalog: ToolCatalog) -> None:
    if not isinstance(value, ast.List):
        report.errors.append('mcp must be a literal list of McpServer("<server>", allow=[...])')
        return
    for item in value.elts:
        parsed = _mcp_server(item)
        if parsed is None:
            report.errors.append(f'mcp entries must be McpServer("<server>", allow=["<tool>", ...]) with literal '
                                 f"strings (got {ast.unparse(item)})")
            continue
        server, allow = parsed
        report.mcp.append(parsed)
        info = catalog.server(server)
        if info is None:
            known = ", ".join(s.name for s in catalog.mcp) or "none"
            report.errors.append(f"unknown MCP server {server!r} (registry servers: {known})")
            continue
        names = {t.name for t in info.tools}
        for tool in allow:
            if tool not in names:
                report.errors.append(f"MCP server {server!r} has no tool {tool!r} (available: "
                                     f"{', '.join(sorted(names))})")


def _mcp_server(item: ast.expr) -> tuple[str, list[str]] | None:
    match item:
        case ast.Call(func=ast.Name(id="McpServer"), args=[ast.Constant(value=str() as server)],
                      keywords=[ast.keyword(arg="allow", value=ast.List(elts=elts))]):
            if all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elts) and elts:
                return server, [e.value for e in elts]
    return None


def _context(value: ast.expr, report: StaticReport, upstream: list[str] | None) -> None:
    if not (isinstance(value, ast.List)
            and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts)):
        report.errors.append("context must be a literal list of strings")
        return
    for e in value.elts:
        report.context.append(e.value)
        try:
            ref = parse_context_entry(e.value)
        except ValueError as err:
            report.errors.append(str(err))
            continue
        if upstream is not None and ref.step is not None and ref.step not in upstream:
            report.errors.append(f"context entry {e.value!r} names {ref.step!r}, which is not an upstream node "
                                 f"(upstream: {', '.join(upstream) or 'none'})")


def _tool_methods(step: ast.ClassDef, report: StaticReport) -> list[ToolMethodInfo]:
    out = []
    for stmt in step.body:
        if not isinstance(stmt, ast.FunctionDef):
            continue
        for decorator in stmt.decorator_list:
            match decorator:
                case ast.Name(id="tool"):
                    out.append(ToolMethodInfo(stmt.name, [], False, []))
                case ast.Call(func=ast.Name(id="tool"), keywords=keywords):
                    out.append(_tool_info(stmt.name, keywords, report))
    return out


def _tool_info(name: str, keywords: list[ast.keyword], report: StaticReport) -> ToolMethodInfo:
    info = ToolMethodInfo(name, [], False, [])
    for kw in keywords:
        try:
            value = ast.literal_eval(kw.value)
        except (ValueError, TypeError, SyntaxError):
            report.errors.append(f"@tool on {name}: {kw.arg}= must be a literal")
            continue
        match kw.arg:
            case "name":
                info.name = value
            case "effects":
                info.effects = list(value)
            case "idempotent":
                info.idempotent = bool(value)
            case "env":
                info.env = list(value)
    return info


def _env_vars(tree: ast.Module) -> set[str]:
    """String-literal names read by `self.runtime.env(…)`, `env(…)`, `os.getenv(…)`, `os.environ.get(…)` and
    `os.environ[…]`."""
    out = set()
    for node in ast.walk(tree):
        match node:
            case ast.Call(func=func, args=[ast.Constant(value=str() as name), *_]) if _reads_env(func):
                out.add(name)
            case ast.Subscript(value=ast.Attribute(value=ast.Name(id="os"), attr="environ"),
                               slice=ast.Constant(value=str() as name)):
                out.add(name)
    return out


def _reads_env(func: ast.expr) -> bool:
    match func:
        case ast.Name(id=name):
            return name in _ENV_FUNCTIONS
        case ast.Attribute(value=ast.Attribute(attr="runtime"), attr="env"):
            return True
        case ast.Attribute(value=ast.Name(id="os"), attr="getenv"):
            return True
        case ast.Attribute(value=ast.Attribute(value=ast.Name(id="os"), attr="environ"), attr="get"):
            return True
    return False


def _runs_subprocess(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=names) if any(a.name.split(".")[0] == "subprocess" for a in names):
                return True
            case ast.ImportFrom(module="subprocess"):
                return True
            case ast.Attribute(value=ast.Name(id="os"), attr="system" | "popen" | "execv" | "execvp" | "spawnv"):
                return True
    return False

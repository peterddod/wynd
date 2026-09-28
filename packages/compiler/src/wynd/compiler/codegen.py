"""Code generation calls, agentic module rendering and attempt files (`$DRAFTS/05 §8.2, §9.8, §13.4`).

Deterministic and shell modules are written in full by the model (their logic is the point) and then checked.
Agentic modules are rendered here from the `write_agentic` answer (docstring, context, tools, mcp, tool methods), so
the model never writes the scaffolding. Every call is one structured-output request: the system prompt is
`preamble.md + "\\n\\n" + <call>.md` with its placeholders filled, the user message is `prompts.render(sections)`.
"""

from __future__ import annotations

import ast
import json
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from wynd.compiler.schemas import models_imports, numbered_examples, render_models_block
from wynd.spec.proto_step import Example
from wynd.spec.typelang import TypeNode

if TYPE_CHECKING:
    from wynd.compiler.attempts import FailureInfo
    from wynd.compiler.llm import CompilerLLM
    from wynd.compiler.tools import ToolCatalog
    from wynd.spec.lockfiles import StepKind

BASES = {"deterministic": "DeterministicStep", "agentic": "AgenticStep", "shell": "ShellStep"}
CASE_LIMIT = 4 * 1024
FAILURES_LIMIT = 30 * 1024
TRACEBACK_LINES = 30


@dataclass
class CodegenContext:
    """Everything the write/revise prompts show about one node."""
    kind: StepKind
    node: str
    package: str                              # package dir name == module name
    class_name: str
    source: str                               # proto file named in generated headers, e.g. proto/<name>.yaml
    instruction: str
    inputs: dict[str, TypeNode]
    outputs: dict[str, dict[str, TypeNode]]
    plain: list[str]                          # schemas.plain_interface lines
    test_file: str
    examples: list[Example]                   # canonical, numbered from 1
    catalog: ToolCatalog
    tier: str = "cheap"                       # agentic: the tier the step will run at
    fixture_files: list[str] = field(default_factory=list)
    previous_source: str | None = None        # the package's current module when recompiling (may hold hand edits)
    guidance: list[str] = field(default_factory=list)
    goal: str | None = None
    upstream: dict[str, str] = field(default_factory=dict)   # upstream node -> its instruction
    deferred: list[int] = field(default_factory=list)        # agentic half of a split: the examples it receives
    program: str = ""                         # shell: the program the decide call named
    exit_codes: dict[int | str, str] | None = None           # shell

    @property
    def module_file(self) -> str:
        return f"{self.package}.py"

    @property
    def models_block(self) -> str:
        return render_models_block(self.inputs, self.outputs)


@dataclass
class Generated:
    """One write/revise answer, with the module source (rendered for agentic steps)."""
    kind: StepKind
    source: str
    deps: list[str] = field(default_factory=list)
    system_packages: list[str] = field(default_factory=list)
    requires_glibc: bool = False
    effects: list[str] = field(default_factory=list)
    env_vars: list[tuple[str, str]] = field(default_factory=list)    # (name, description)
    notes: str = ""
    capabilities: dict[str, Any] | None = None     # agentic: {docstring, context, tools, mcp, tool_methods}
    diagnosis: str | None = None                   # revisions only
    verdict: str | None = None
    suspects: list[tuple[int, str]] = field(default_factory=list)


def exit_codes_text(codes: Mapping[int | str, str] | None) -> str:
    codes = codes or {0: "done", "*": "error"}
    return "{" + ", ".join(f"{k!r}: {v!r}" for k, v in codes.items()) + "}"


def write(llm: CompilerLLM, ctx: CodegenContext) -> Generated:
    """The first attempt's module: `write_deterministic`, `write_shell` or `write_agentic`."""
    match ctx.kind:
        case "agentic":
            kind, sections = "write_agentic", _agentic_sections(ctx)
        case "shell":
            kind, sections = "write_shell", _code_sections(ctx)
        case _:
            kind, sections = "write_deterministic", _code_sections(ctx)
    return _generated(ctx, _call(llm, kind, ctx, sections))


def revise(llm: CompilerLLM, ctx: CodegenContext, previous: Generated, failures: Sequence[FailureInfo],
           diagnoses: Sequence[str], *, attempt: int, attempts: int) -> Generated:
    """A revision after failing tests (`revise_code` / `revise_agentic`); `attempt` of `attempts` is the attempt the
    revision will become."""
    from wynd.compiler.prompts import TIER_WORDS, Code

    progress = f"Attempt {attempt} of {attempts}"
    if ctx.kind == "agentic":
        sections: list[tuple[str, Any]] = [
            ("Current docstring and capabilities", previous.capabilities or {}),
            ("Failures", render_failures(failures)),
            ("Earlier diagnoses", list(diagnoses) or "none"),
            ("Attempt", progress),
            ("Model tier", TIER_WORDS[ctx.tier]),
        ]
        response = _call(llm, "revise_agentic", ctx, sections)
    else:
        sections = [
            ("Current module", Code(previous.source)),
            ("Test file", Code(ctx.test_file)),
            ("Failures", render_failures(failures)),
            ("Earlier diagnoses", list(diagnoses) or "none"),
            ("Attempt", progress),
        ]
        response = _call(llm, "revise_code", ctx, sections)
    out = _generated(ctx, response)
    out.diagnosis, out.verdict = response.diagnosis, response.verdict
    out.suspects = [(s.example, s.why) for s in response.suspects]
    return out


def attempt_files(ctx: CodegenContext, generated: Generated) -> dict[str, str]:
    """The files one attempt writes into the package: the module and the generated test file."""
    return {ctx.module_file: generated.source, f"test_{ctx.package}.py": ctx.test_file}


def _call(llm: CompilerLLM, kind: str, ctx: CodegenContext, sections: list[tuple[str, Any]]) -> Any:
    from wynd.compiler.calls import CALLS
    from wynd.compiler.prompts import TIER_WORDS, render, split_note, system_prompt

    spec = CALLS[kind]
    deferred = ", ".join(str(n) for n in ctx.deferred)
    values = {
        "class_name": ctx.class_name,
        "program": ctx.program,
        "exit_codes": exit_codes_text(ctx.exit_codes),
        "tier_words": TIER_WORDS[ctx.tier],
        "split_note": split_note(f"examples {deferred}") if ctx.deferred else "",
    }
    return llm.call(kind, node=ctx.node, system=system_prompt(kind, **values), prompt=render(sections),
                    response_model=spec.response_model, tier=spec.tier, thinking=spec.thinking).value


def _generated(ctx: CodegenContext, response: Any) -> Generated:
    env_vars = [(v.name, v.description) for v in response.env_vars]
    if ctx.kind != "agentic":
        return Generated(kind=ctx.kind, source=response.module_source, deps=list(response.deps),
                         system_packages=list(response.system_packages), requires_glibc=response.requires_glibc,
                         effects=list(response.effects), env_vars=env_vars, notes=response.notes)
    mcp = [(m.server, list(m.allow)) for m in response.mcp]
    source = render_agentic(class_name=ctx.class_name, source=ctx.source, docstring=response.docstring,
                            inputs=ctx.inputs, outputs=ctx.outputs, context=response.context, tools=response.tools,
                            mcp=mcp, tool_methods=response.tool_methods)
    capabilities = {"docstring": response.docstring, "context": list(response.context),
                    "tools": list(response.tools), "mcp": [{"server": s, "allow": a} for s, a in mcp],
                    "tool_methods": response.tool_methods}
    return Generated(kind="agentic", source=source, deps=list(response.deps), env_vars=env_vars,
                     notes=response.notes, capabilities=capabilities)


# --- prompt sections ------------------------------------------------------------------------------------------------

def _contract(ctx: CodegenContext) -> str:
    return ctx.instruction.strip() + "\n\n" + "\n".join(ctx.plain)


def _code_sections(ctx: CodegenContext) -> list[tuple[str, Any]]:
    from wynd.compiler.prompts import Code, runtime_api

    sections: list[tuple[str, Any]] = [
        ("Runtime API reference", runtime_api()),
        ("Step contract", _contract(ctx)),
        ("Class name and base", f"{ctx.class_name}({BASES[ctx.kind]})"),
        ("Models block (include verbatim)", Code(ctx.models_block)),
        ("Test file", Code(ctx.test_file)),
    ]
    if ctx.fixture_files:
        sections.append(("Fixture files", list(ctx.fixture_files)))
    if ctx.previous_source:
        sections.append(("Previous implementation (may contain hand edits; keep them where still consistent)",
                         Code(ctx.previous_source)))
    if ctx.guidance:
        sections.append(("User guidance", list(ctx.guidance)))
    if ctx.kind == "shell":
        sections.append(("Exit codes", exit_codes_text(ctx.exit_codes)))
        sections.append(("Program", ctx.program))
    return sections


def _agentic_sections(ctx: CodegenContext) -> list[tuple[str, Any]]:
    from wynd.compiler.prompts import TIER_WORDS, runtime_api

    sections: list[tuple[str, Any]] = [
        ("Runtime API reference", runtime_api()),
        ("Step contract", _contract(ctx)),
        ("Examples (for deriving rules only)", numbered_examples(ctx.examples)),
        ("Built-in tools", [b.line() for b in ctx.catalog.builtins] or "none"),
        ("MCP servers", {s.name: s.summary() for s in ctx.catalog.mcp} or "none"),
        ("Upstream nodes", dict(ctx.upstream) or "none"),
        ("Process goal", ctx.goal or "none"),
        ("Model tier", TIER_WORDS[ctx.tier]),
    ]
    if ctx.deferred:
        sections.append(("Split note", f"This step receives the inputs the deterministic half defers "
                                       f"(examples {', '.join(str(n) for n in ctx.deferred)})."))
    previous = class_docstring(ctx.previous_source, ctx.class_name) if ctx.previous_source else None
    if previous:
        sections.append(("Previous docstring", previous))
    if ctx.guidance:
        sections.append(("User guidance", list(ctx.guidance)))
    return sections


def class_docstring(source: str, class_name: str) -> str | None:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return ast.get_docstring(node)
    return None


def render_failures(failures: Sequence[FailureInfo]) -> str:
    """The Failures section: per case the test, example number, expected and actual exit/outputs (from
    `WYND-EXPECT`) or the error and the end of its traceback; 4 KB per case, 30 KB in all."""
    parts: list[str] = []
    total = 0
    for f in failures:
        text = _failure_text(f)
        if len(text) > CASE_LIMIT:
            text = text[:CASE_LIMIT] + "\n…[truncated]"
        if total + len(text) > FAILURES_LIMIT:
            parts.append(f"…[{len(failures) - len(parts)} more failing cases not shown]")
            break
        parts.append(text)
        total += len(text)
    return "\n\n".join(parts) if parts else "none"


def _failure_text(f: FailureInfo) -> str:
    head = f"### {f.test}" + (f" (example {f.example})" if f.example is not None else "")
    if f.outcome == "deferred":
        message = ((f.error or {}).get("message") or "").removeprefix("NotImplementedError: ")
        return f"{head}: deferred (raised NotImplementedError: {message})"
    lines = [head]
    if f.expected_exit is not None:
        lines.append(f"expected exit: {f.expected_exit}")
        lines.append(f"actual exit: {f.actual_exit}")
        for m in f.mismatches:
            lines.append(f"- {m.get('field')}: expected {_json(m.get('expected'))}, got {_json(m.get('actual'))}")
        if f.error:
            lines.append(f"error ({f.error.get('cause')}, {f.error.get('error_type')}): {f.error.get('message')}")
    else:
        lines.append("\n".join((f.message or "").splitlines()[-TRACEBACK_LINES:]))
    return "\n".join(lines)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# --- agentic module rendering ---------------------------------------------------------------------------------------

def render_agentic(
    *,
    class_name: str,
    source: str,
    docstring: str,
    inputs: Mapping[str, TypeNode],
    outputs: Mapping[str, Mapping[str, TypeNode]],
    context: Sequence[str] = (),
    tools: Sequence[str] = (),
    mcp: Sequence[tuple[str, Sequence[str]]] = (),
    tool_methods: str = "",
) -> str:
    """The whole AgenticStep module; imports of builtin tools, `McpServer` and `tool` only when used."""
    methods = textwrap.indent(textwrap.dedent(tool_methods).strip("\n"), "    ") if tool_methods.strip() else ""
    runtime_names = ["AgenticStep"]
    if mcp:
        runtime_names.append("McpServer")
    if "@tool" in methods:
        runtime_names.append("tool")
    lines = [f'"""Generated by wynd compile from {source}. Hand edits are kept until the proto-step changes."""']
    lines += models_imports(inputs, outputs)
    lines += ["", f"from wynd.runtime import {', '.join(runtime_names)}"]
    if tools:
        lines.append(f"from wynd.runtime.tools import {', '.join(sorted(set(tools)))}")
    lines += ["", "", f"class {class_name}(AgenticStep):", _docstring_block(docstring), ""]
    body = render_models_block(inputs, outputs)
    body += "\n"
    body += f"    context = {json.dumps(list(context), ensure_ascii=False)}\n"
    body += f"    tools = [{', '.join(tools)}]\n"
    servers = ", ".join(f"McpServer({json.dumps(server)}, allow={json.dumps(list(allow))})" for server, allow in mcp)
    body += f"    mcp = [{servers}]\n"
    if methods:
        body += "\n" + methods + "\n"
    body += "\n    def run(self, input: Input) -> Output: ...\n"
    return "\n".join(lines) + "\n" + body


def _docstring_block(text: str) -> str:
    text = text.strip().replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    if text.endswith('"'):
        text += " "
    lines = text.splitlines() or [""]
    if len(lines) == 1:
        return f'    """{lines[0]}"""'
    rest = [("    " + line) if line.strip() else "" for line in lines[1:]]
    return "\n".join([f'    """{lines[0]}', *rest, '    """'])

"""astcheck: class shape, agentic attribute extraction, env scan, unknown tools/servers/context, subprocess use."""

import textwrap

from wynd.compiler.astcheck import ToolMethodInfo, check, lint_errors
from wynd.compiler.tools import BuiltinToolInfo, McpServerInfo, ToolCatalog
from wynd.runtime.mcp.entry import McpServerEntry
from wynd.runtime.mcp.snapshot import McpToolSpec

CATALOG = ToolCatalog(
    builtins=[BuiltinToolInfo(name, f"{name}()", "", effects, idempotent, env) for name, effects, idempotent, env in (
        ("web_search", ["network"], True, ["BRAVE_API_KEY"]), ("now", [], False, []), ("shell", ["shell"], False, []))],
    mcp=[McpServerInfo("github", McpServerEntry(name="github", transport="http", url="https://x.invalid/mcp"),
                       [McpToolSpec("get_issue", "Get an issue", {"type": "object"}, {}),
                        McpToolSpec("list_issues", "List issues", {"type": "object"}, {})])],
)


def agentic(body: str, header: str = "") -> str:
    return textwrap.dedent(f'''\
        from typing import Literal

        from pydantic import BaseModel

        from wynd.runtime import AgenticStep, McpServer, tool
        from wynd.runtime.tools import now, shell, web_search
        {header}

        class Classify(AgenticStep):
            """Classify it."""

            class Input(BaseModel):
                text: str

            class Done(BaseModel):
                exit: Literal["done"] = "done"

            Output = Done
        ''') + textwrap.indent(textwrap.dedent(body), "    ")


def run(source: str, **kwargs):
    kwargs.setdefault("class_name", "Classify")
    kwargs.setdefault("base", "AgenticStep")
    kwargs.setdefault("catalog", CATALOG)
    kwargs.setdefault("upstream_nodes", ["read", "extract"])
    return check(source, **kwargs)


GOOD = '''
context = ["process.goal", "steps.extract.outputs.total"]
tools = [web_search, now, shell.allow("pdftotext", "qpdf")]
mcp = [McpServer("github", allow=["get_issue"])]

@tool(effects=["network"], idempotent=True, env=["CRM_TOKEN"])
def lookup(self, key: str) -> str:
    """Look up a key."""
    return self.runtime.env("CRM_TOKEN")

@tool
def plain(self) -> str:
    """Plain."""
    return ""

@tool(name="renamed", effects=["filesystem"])
def other(self) -> str:
    """Other."""
    return ""

def run(self, input: Input) -> Output: ...
'''


def test_extracts_agentic_attributes():
    report = run(agentic(GOOD))
    assert report.errors == []
    assert report.context == ["process.goal", "steps.extract.outputs.total"]
    assert report.tools == ["web_search", "now", "shell"]
    assert report.shell_allow == ["pdftotext", "qpdf"]
    assert report.mcp == [("github", ["get_issue"])]
    assert report.tool_methods == [
        ToolMethodInfo("lookup", ["network"], True, ["CRM_TOKEN"]),
        ToolMethodInfo("plain", [], False, []),
        ToolMethodInfo("renamed", ["filesystem"], False, []),
    ]
    assert report.env_vars == {"CRM_TOKEN"}


def test_env_scan_all_forms():
    source = textwrap.dedent('''\
        import os
        from wynd.runtime import DeterministicStep, env

        class Load(DeterministicStep):
            def run(self, input):
                a = self.runtime.env("A_VAR")
                b = env("B_VAR")
                c = os.environ["C_VAR"]
                d = os.environ.get("D_VAR", "x")
                e = os.getenv("E_VAR")
                name = "DYNAMIC"
                f = os.getenv(name)
                return a
        ''')
    report = run(source, class_name="Load", base="DeterministicStep")
    assert report.errors == []
    assert report.env_vars == {"A_VAR", "B_VAR", "C_VAR", "D_VAR", "E_VAR"}


def test_class_shape_errors():
    none = "x = 1\n"
    assert "no step class" in run(none).errors[0]
    two = agentic("def run(self, input: Input) -> Output: ...\n") + "\n\nclass Second(AgenticStep):\n    pass\n"
    assert any("2 step classes" in e for e in run(two).errors)
    wrong_name = run(agentic("def run(self, input: Input) -> Output: ...\n"), class_name="Other")
    assert wrong_name.errors == ["the step class must be named Other (found Classify)"]
    wrong_base = run(agentic("def run(self, input: Input) -> Output: ...\n"), base="DeterministicStep")
    assert wrong_base.errors == ["Classify must derive from DeterministicStep (bases: AgenticStep)"]


def test_agentic_run_body_must_be_ellipsis():
    report = run(agentic("def run(self, input: Input) -> Output:\n    return self.Done()\n"))
    assert report.errors == ["an AgenticStep's run body must be exactly `...` (the model runs it)"]


def test_unknown_tool_server_and_mcp_tool():
    report = run(agentic('''
        tools = [fetch_everything]
        mcp = [McpServer("jira", allow=["x"]), McpServer("github", allow=["delete_repo"])]
        def run(self, input: Input) -> Output: ...
        '''))
    assert any("unknown builtin tool 'fetch_everything'" in e for e in report.errors)
    assert any("unknown MCP server 'jira'" in e for e in report.errors)
    assert any("has no tool 'delete_repo'" in e for e in report.errors)


def test_non_literal_attributes_and_bare_shell():
    report = run(agentic('''
        tools = [shell] + []
        mcp = [McpServer(name, allow=ALLOWED)]
        context = ["process.goal", GOAL]
        def run(self, input: Input) -> Output: ...
        '''))
    assert report.errors == [
        "tools must be a literal list of builtin tool names",
        'mcp entries must be McpServer("<server>", allow=["<tool>", ...]) with literal strings '
        "(got McpServer(name, allow=ALLOWED))",
        "context must be a literal list of strings",
    ]
    bare = run(agentic("tools = [shell]\ndef run(self, input: Input) -> Output: ...\n"))
    assert bare.errors == ['the shell builtin needs an allowlist of executables: shell.allow("<executable>", ...)']


def test_context_entries():
    report = run(agentic('''
        context = ["previous.nonsense", "steps.save.outputs"]
        def run(self, input: Input) -> Output: ...
        '''))
    assert len(report.errors) == 2
    assert "invalid context entry 'previous.nonsense'" in report.errors[0]
    assert "names 'save', which is not an upstream node" in report.errors[1]
    unchecked = run(agentic('context = ["steps.save.outputs"]\ndef run(self, input: Input) -> Output: ...\n'),
                    upstream_nodes=None)
    assert unchecked.errors == []


def test_subprocess_needs_shell_effect():
    source = textwrap.dedent('''\
        import subprocess
        from wynd.runtime import DeterministicStep

        class Convert(DeterministicStep):
            def run(self, input):
                return subprocess.run(["true"])
        ''')
    kwargs = {"class_name": "Convert", "base": "DeterministicStep"}
    assert run(source, effects=["filesystem"], **kwargs).errors == [
        "the module runs subprocesses (subprocess / os.system) but does not declare the 'shell' effect"]
    assert run(source, effects=["shell"], **kwargs).errors == []
    assert run(source, **kwargs).errors == []                        # effects unknown: not checked
    os_system = source.replace("import subprocess\n", "import os\n").replace('subprocess.run(["true"])',
                                                                              'os.system("true")')
    assert run(os_system, effects=[], **kwargs).errors


def test_syntax_error_and_lint():
    assert run("class Broken(:\n").errors[0].startswith("the module does not parse: line 1")
    assert lint_errors("class Broken(:\n", "broken.py")[0].startswith("broken.py: the module does not parse")
    source = "from wynd.runtime import DeterministicStep\nCACHE = {}\n\nclass S(DeterministicStep):\n    pass\n"
    errors = lint_errors(source, "s.py")
    assert len(errors) == 1 and errors[0].startswith("s.py:2:") and " L002 " in errors[0]

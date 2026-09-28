"""codegen: agentic module rendering (golden, lint-clean, describes to the models block), write/revise calls and
their prompts, the failures section."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from wynd.compiler.astcheck import check, lint_errors
from wynd.compiler.attempts import FailureInfo
from wynd.compiler.calls import (
    EnvVar,
    McpUse,
    ReviseAgenticResponse,
    ReviseCodeResponse,
    Suspect,
    WriteAgenticResponse,
    WriteCodeResponse,
)
from wynd.compiler.codegen import (
    CodegenContext,
    attempt_files,
    class_docstring,
    render_agentic,
    render_failures,
    revise,
    write,
)
from wynd.compiler.tools import BuiltinToolInfo, McpServerInfo, ToolCatalog, builtin_catalog
from wynd.runtime.describe import describe_class
from wynd.runtime.mcp.entry import McpServerEntry
from wynd.runtime.mcp.snapshot import McpToolSpec
from wynd.spec.interface import interface_from_fields, interfaces_equivalent
from wynd.spec.proto_step import Example
from wynd.spec.typelang import parse_type

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "codegen"
INPUTS = {"invoice_text": parse_type("string")}
OUTPUTS = {"done": {"supplier": parse_type("string"), "total": parse_type("number"), "due_date": parse_type("date")},
           "not_an_invoice": {}}
DOCSTRING = """Extract the key payment fields from the text of a supplier invoice.

Choose `done` when the text is an invoice requesting payment. Choose `not_an_invoice` for anything else.

- supplier: the name of the company that issued the invoice.
- total: the final amount due as a plain number."""
TOOL_METHOD = '''\
    @tool(effects=["network"], idempotent=True, env=["CRM_TOKEN"])
    def lookup_supplier(self, name: str) -> str:
        """Find the supplier's canonical name."""
        return name
'''
CATALOG = ToolCatalog(
    builtins=builtin_catalog().builtins,
    mcp=[McpServerInfo("github", McpServerEntry(name="github", transport="http", url="https://x.invalid/mcp",
                                                description="GitHub"),
                       [McpToolSpec("get_issue", "Get an issue", {"type": "object"}, {})])],
)


def load(source: str, tmp_path: Path):
    """Import the module from a file (the runtime reads run()'s source)."""
    path = tmp_path / "extract_invoice_fields.py"
    path.write_text(source)
    spec = importlib.util.spec_from_file_location(f"generated_{tmp_path.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ExtractInvoiceFields


def test_render_agentic_golden_and_checks(tmp_path):
    source = render_agentic(class_name="ExtractInvoiceFields", source="proto/extract_invoice_fields.yaml",
                            docstring=DOCSTRING, inputs=INPUTS, outputs=OUTPUTS, context=["process.goal"],
                            tools=["web_search", "now"], mcp=[("github", ["get_issue"])], tool_methods=TOOL_METHOD)
    assert source == (GOLDEN / "agentic.py.golden").read_text()
    assert lint_errors(source, "extract_invoice_fields.py") == []
    report = check(source, class_name="ExtractInvoiceFields", base="AgenticStep", catalog=CATALOG, upstream_nodes=[])
    assert report.errors == []
    assert report.tools == ["web_search", "now"] and report.mcp == [("github", ["get_issue"])]
    assert report.env_vars == {"CRM_TOKEN"}
    described = describe_class(load(source, tmp_path))
    assert interfaces_equivalent(interface_from_fields(INPUTS, OUTPUTS), described.interface) == []
    assert described.context == ["process.goal"]
    assert described.doc.startswith("Extract the key payment fields")
    assert [t.name for t in described.tools] == ["lookup_supplier", "web_search", "now"]


def test_render_agentic_minimal(tmp_path):
    source = render_agentic(class_name="ExtractInvoiceFields", source="proto/x.yaml", docstring='Say "hi"',
                            inputs=INPUTS, outputs=OUTPUTS)
    assert "from wynd.runtime import AgenticStep\n" in source
    assert "wynd.runtime.tools" not in source and "McpServer" not in source
    assert '    """Say "hi" """' in source
    assert "    context = []\n    tools = []\n    mcp = []\n\n" in source
    assert source.endswith("    def run(self, input: Input) -> Output: ...\n")
    described = describe_class(load(source, tmp_path))
    assert described.kind == "agentic" and described.tools == [] and described.mcp == []


class FakeLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def call(self, kind, *, node, system, prompt, response_model, tier, thinking):
        self.calls.append(SimpleNamespace(kind=kind, node=node, system=system, prompt=prompt, model=response_model,
                                          tier=tier, thinking=thinking))
        answer = self.answers.pop(0)
        assert isinstance(answer, response_model)
        return SimpleNamespace(value=answer)


def context(kind: str, **extra) -> CodegenContext:
    values = dict(
        kind=kind, node="extract", package="extract_invoice_fields", class_name="ExtractInvoiceFields",
        source="proto/extract_invoice_fields.yaml", instruction="Extract the fields.", inputs=INPUTS,
        outputs=OUTPUTS, plain=["extract_invoice_fields takes:", "  - invoice_text — text"],
        test_file="def test_example_1(tmp_path):\n    pass\n",
        examples=[Example(inputs={"invoice_text": "INV-1"}, outputs={"supplier": "ACME"})], catalog=CATALOG,
    )
    values.update(extra)
    return CodegenContext(**values)


CODE = WriteCodeResponse(module_source="# module\n", deps=["pypdf>=6"], system_packages=[], requires_glibc=False,
                         effects=["filesystem"], env_vars=[EnvVar(name="PDF_PASSWORD", description="pw")],
                         notes="Reads the PDF.")


def test_write_deterministic():
    llm = FakeLLM(CODE)
    ctx = context("deterministic", fixture_files=["examples/a.pdf"], previous_source="# old\n",
                  guidance=["treat credit notes as invoices"])
    generated = write(llm, ctx)
    assert generated.source == "# module\n" and generated.kind == "deterministic"
    assert generated.deps == ["pypdf>=6"] and generated.effects == ["filesystem"]
    assert generated.env_vars == [("PDF_PASSWORD", "pw")]
    [call] = llm.calls
    assert (call.kind, call.node, call.tier, call.thinking) == ("write_deterministic", "extract", "strong", "medium")
    assert "ExtractInvoiceFields(DeterministicStep)" in call.system
    for section in ("## Runtime API reference", "## Step contract", "## Class name and base",
                    "## Models block (include verbatim)", "## Test file", "## Fixture files",
                    "## Previous implementation", "## User guidance"):
        assert section in call.prompt
    assert ctx.models_block.rstrip() in call.prompt
    assert "treat credit notes as invoices" in call.prompt
    assert attempt_files(ctx, generated) == {"extract_invoice_fields.py": "# module\n",
                                             "test_extract_invoice_fields.py": ctx.test_file}


def test_write_shell_fills_program_and_exit_codes():
    llm = FakeLLM(CODE)
    write(llm, context("shell", program="pdftotext", exit_codes={0: "done", "*": "error"}))
    [call] = llm.calls
    assert call.kind == "write_shell"
    assert '"pdftotext"' in call.system and "{0: 'done', '*': 'error'}" in call.system
    assert "## Exit codes" in call.prompt and "## Program" in call.prompt


AGENTIC = WriteAgenticResponse(docstring=DOCSTRING, context=["process.goal"], tools=["web_search"],
                               mcp=[McpUse(server="github", allow=["get_issue"])], tool_methods="", deps=[],
                               env_vars=[], notes="Needs reading comprehension.")


def test_write_agentic_renders_module():
    llm = FakeLLM(AGENTIC)
    ctx = context("agentic", tier="standard", deferred=[4, 5], upstream={"read": "Read the PDF."}, goal="Pay bills")
    generated = write(llm, ctx)
    assert generated.source == render_agentic(class_name="ExtractInvoiceFields", source=ctx.source,
                                              docstring=DOCSTRING, inputs=INPUTS, outputs=OUTPUTS,
                                              context=["process.goal"], tools=["web_search"],
                                              mcp=[("github", ["get_issue"])])
    assert generated.capabilities["mcp"] == [{"server": "github", "allow": ["get_issue"]}]
    [call] = llm.calls
    assert call.kind == "write_agentic"
    assert "a mid-sized language model" in call.system
    assert "examples 4, 5" in call.system                         # the split note
    for section in ("## Examples (for deriving rules only)", "## Built-in tools", "## MCP servers",
                    "## Upstream nodes", "## Process goal", "## Model tier", "## Split note"):
        assert section in call.prompt
    assert "Pay bills" in call.prompt and "Read the PDF." in call.prompt


def test_write_agentic_without_split_has_no_note():
    llm = FakeLLM(AGENTIC)
    write(llm, context("agentic"))
    assert "This step is a fallback" not in llm.calls[0].system
    assert "## Split note" not in llm.calls[0].prompt


FAILURES = [
    FailureInfo(test="test_example_2", example=2, outcome="deferred",
                error={"cause": "exception", "message": "NotImplementedError: amount in words",
                       "error_type": "NotImplementedError"}),
    FailureInfo(test="test_example_3", example=3, outcome="wrong", expected_exit="done", actual_exit="done",
                mismatches=[{"field": "total", "expected": 12.5, "actual": 125.0}]),
    FailureInfo(test="(import)", example=None, outcome="wrong", message="\n".join(f"line {i}" for i in range(40))),
]


def test_revise_code():
    answer = ReviseCodeResponse(**CODE.model_dump(), diagnosis="The decimal point is dropped.", verdict="fixable",
                                suspects=[Suspect(example=3, why="odd")])
    llm = FakeLLM(answer)
    ctx = context("deterministic")
    previous = write(FakeLLM(CODE), ctx)
    out = revise(llm, ctx, previous, FAILURES, ["earlier idea"], attempt=2, attempts=4)
    assert (out.diagnosis, out.verdict, out.suspects) == ("The decimal point is dropped.", "fixable",
                                                          [(3, "odd")])
    [call] = llm.calls
    assert (call.kind, call.tier, call.thinking) == ("revise_code", "strong", "high")
    assert "Attempt 2 of 4" in call.prompt and "earlier idea" in call.prompt
    assert "deferred (raised NotImplementedError: amount in words)" in call.prompt
    assert "## Current module" in call.prompt and "# module" in call.prompt


def test_revise_agentic():
    answer = ReviseAgenticResponse(**AGENTIC.model_dump(), diagnosis="Unclear exit rule.",
                                   verdict="needs_stronger_model", suspects=[])
    llm = FakeLLM(answer)
    ctx = context("agentic")
    previous = write(FakeLLM(AGENTIC), ctx)
    out = revise(llm, ctx, previous, FAILURES[1:2], [], attempt=2, attempts=4)
    assert out.verdict == "needs_stronger_model" and out.source == previous.source
    [call] = llm.calls
    assert call.kind == "revise_agentic"
    assert "## Current docstring and capabilities" in call.prompt and "get_issue" in call.prompt
    assert "a small, fast language model" in call.prompt


def test_render_failures():
    text = render_failures(FAILURES)
    assert text.splitlines()[0] == "### test_example_2 (example 2): deferred (raised NotImplementedError: amount in " \
                                   "words)"
    assert "expected exit: done" in text and "- total: expected 12.5, got 125.0" in text
    assert "line 9\n" not in text and "line 10" in text and "line 39" in text     # the last 30 traceback lines
    assert render_failures([]) == "none"
    many = [FailureInfo(test=f"t{i}", example=i, outcome="wrong", message="x" * 5000) for i in range(20)]
    capped = render_failures(many)
    assert len(capped) < 31 * 1024 and "more failing cases not shown" in capped
    assert "…[truncated]" in capped


@pytest.mark.parametrize(("source", "expected"), [
    ('class A:\n    """Doc."""\n', "Doc."),
    ("class A:\n    pass\n", None),
    ("class A(:\n", None),
])
def test_class_docstring(source, expected):
    assert class_docstring(source, "A") == expected


def test_builtin_line_used_in_agentic_prompt():
    llm = FakeLLM(AGENTIC)
    catalog = ToolCatalog(builtins=[BuiltinToolInfo("clock", "clock()", "The time.", [], False, [])], mcp=[])
    write(llm, context("agentic", catalog=catalog))
    assert "- clock() — The time. [effects: none]" in llm.calls[0].prompt

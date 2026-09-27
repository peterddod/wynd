"""lockfile: golden locks per kind (validated by the spec StepLock), effects/vars/deps rules, the pyproject marker,
and the M1 dogfood locks reproduced from their protos and modules."""

from pathlib import Path

import pytest

from wynd.compiler.astcheck import StaticReport, check
from wynd.compiler.lockfile import (
    CompiledInfo,
    CompiledSplit,
    build_step_lock,
    compiled_info,
    locked_deps,
    merge_deps,
    project_name,
    render_pyproject,
    render_step_lock,
)
from wynd.compiler.tools import BuiltinToolInfo, McpServerInfo, ToolCatalog, builtin_catalog
from wynd.runtime.mcp.entry import McpServerEntry
from wynd.runtime.mcp.snapshot import McpToolSpec
from wynd.spec.hashing import proto_hash
from wynd.spec.lockfiles import DEFAULT_RETRIES, StepLock, load_step_lock
from wynd.spec.proto_step import ProtoStep, load_proto_step
from wynd.spec.workspace import local_step_id
from wynd.spec.yamlio import parse_model

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "lockfile"
PROCESS = Path(__file__).resolve().parents[3] / "examples/invoices/processes/process_supplier_invoice"
PID = "process_supplier_invoice"

CATALOG = ToolCatalog(
    builtins=[BuiltinToolInfo("web_search", "web_search(query: string)", "Search.", ["network"], True,
                              ["BRAVE_API_KEY"]),
              BuiltinToolInfo("shell", "shell(command: list[string])", "Run.", ["shell"], False, [])],
    mcp=[McpServerInfo("github", McpServerEntry(name="github", transport="http", url="https://x.invalid/mcp",
                                                auth_env=["GITHUB_TOKEN"]),
                       [McpToolSpec("get_issue", "Get an issue", {"type": "object"}, {"readOnlyHint": True})])],
)
COMPILED = CompiledInfo(session="job_1", job="job_2", compiler="wynd-compiler 0.1.0", model="claude-code/strong",
                        decision={"kind": "agentic", "rule": 2, "reason": "needs judgement"},
                        rejected=["What if the total is in words?"])


def proto(**extra) -> ProtoStep:
    doc = {"kind": "proto_step", "name": "classify", "instruction": "Classify.", "inputs": {"text": "string"},
           "outputs": {"label": "string"}, **extra}
    return ProtoStep.model_validate(doc)


def static(**kwargs) -> StaticReport:
    base = {"errors": [], "env_vars": set(), "tools": [], "mcp": [], "context": [], "tool_methods": []}
    return StaticReport(**{**base, **kwargs})


def golden(name: str, text: str) -> None:
    assert text == (GOLDEN / name).read_text()
    parse_model(text, StepLock)                              # the rendered lock validates (E-LOCK rules included)


def agentic_lock() -> StepLock:
    from wynd.compiler.astcheck import ToolMethodInfo

    p = proto(env={"vars": [{"name": "REGION", "description": "Deployment region", "required": False}]})
    return build_step_lock(
        name="classify", kind="agentic", entrypoint="classify:Classify", step_id="proc#classify", proto=p,
        proto_hash=proto_hash(p), interface=p.interface(), catalog=CATALOG,
        static=static(tools=["web_search"], mcp=[("github", ["get_issue"])], context=["process.goal"],
                      tool_methods=[ToolMethodInfo("crm", ["filesystem"], True, ["CRM_TOKEN"])],
                      env_vars={"CRM_TOKEN", "REGION"}),
        deps=["httpx>=0.27"], env_vars=[("CRM_TOKEN", "CRM API token")], compiled=COMPILED,
    )


def test_agentic_golden():
    lock = agentic_lock()
    golden("agentic.step.lock.yaml", render_step_lock(lock))
    assert lock.tier == "cheap" and lock.thinking == "low" and lock.provider is None
    assert lock.retries == DEFAULT_RETRIES["agentic"]
    assert lock.effects == ["network", "filesystem"]              # tool effects ∪ network for mcp, ordered
    assert [(v.name, v.used_by, v.secret) for v in lock.fragment.vars] == [
        ("BRAVE_API_KEY", ["tool:proc#classify/web_search"], False),
        ("CRM_TOKEN", ["step:proc#classify", "tool:proc#classify/crm"], False),
        ("GITHUB_TOKEN", ["mcp:github"], True),
        ("REGION", ["step:proc#classify"], False),
    ]
    crm = next(v for v in lock.fragment.vars if v.name == "CRM_TOKEN")
    assert crm.description == "CRM API token"
    region = next(v for v in lock.fragment.vars if v.name == "REGION")
    assert region.required is False and region.description == "Deployment region"   # the proto's var is kept
    assert compiled_info(lock) == COMPILED


def test_deterministic_golden():
    p = proto(env={"deps": ["pypdf>=6,<7"], "system": ["poppler-utils"]})
    lock = build_step_lock(
        name="classify", kind="deterministic", entrypoint="classify:Classify", step_id="proc#classify", proto=p,
        proto_hash=proto_hash(p), interface=p.interface(), catalog=CATALOG,
        static=static(env_vars={"PDF_PASSWORD"}, context=["process.goal"], tools=["web_search"]),
        deps=["PyPDF==5.0", "requests>=2"], system_packages=["qpdf"], effects=["filesystem"],
        env_vars=[("PDF_PASSWORD", "Password for encrypted PDFs")], locked=["pypdf==6.19.0"],
    )
    golden("deterministic.step.lock.yaml", render_step_lock(lock))
    assert lock.fragment.deps == ["pypdf>=6,<7", "requests>=2"]          # the proto's specifier wins
    assert lock.fragment.system == ["poppler-utils", "qpdf"]
    assert lock.tools == [] and lock.context == [] and lock.tier is None   # agentic-only fields stay empty
    assert lock.retries == DEFAULT_RETRIES["deterministic"]


def test_shell_golden():
    p = proto(exit_codes={0: "done", 3: "done", "*": "error"})
    lock = build_step_lock(name="classify", kind="shell", entrypoint="classify:Classify", step_id="proc#classify",
                           proto=p, proto_hash=proto_hash(p), interface=p.interface(), catalog=CATALOG,
                           static=static(), system_packages=["coreutils"], requires_glibc=True)
    golden("shell.step.lock.yaml", render_step_lock(lock))
    assert lock.effects == ["shell"]
    assert lock.shell.exit_codes == {0: "done", 3: "done", "*": "error"}
    assert lock.fragment.requires == "glibc"
    default = build_step_lock(name="c", kind="shell", entrypoint="c:C", step_id="p#c", proto=None, proto_hash=None,
                              interface=None, catalog=CATALOG, static=static())
    assert default.shell.exit_codes == {0: "done", "*": "error"}


def test_mcp_without_network_effect_declared_still_valid():
    lock = build_step_lock(name="c", kind="agentic", entrypoint="c:C", step_id="p#c", proto=None, proto_hash=None,
                           interface=None, catalog=CATALOG, static=static(mcp=[("github", ["get_issue"])]))
    assert lock.effects == ["network"]
    assert lock.mcp[0].allow == ["get_issue"]


def test_merge_deps_and_locked_deps():
    assert merge_deps(["pypdf>=6,<7"], ["PyPDF>=5", "Requests", "wynd-runtime", "requests>=3"]) == [
        "pypdf>=6,<7", "Requests"]
    assert locked_deps([], lambda deps: pytest.fail("no resolve without deps")) == []
    pins = ["requests==2.32.0", "wynd_spec==0.1.0", "certifi==2026.1.1", "wynd-runtime==0.1.0"]
    assert locked_deps(["requests"], lambda deps: pins) == ["certifi==2026.1.1", "requests==2.32.0"]


def test_pyproject_golden():
    text = render_pyproject(project_name("finance/invoices", "read_pdf"), ["pypdf>=6,<7", "requests>=2"])
    assert text == (GOLDEN / "pyproject.toml.golden").read_text()


def test_compiled_split_roundtrip():
    info = COMPILED.model_copy(update={"split": CompiledSplit(role="deterministic", node="extract",
                                                              partner="extract_agentic_pkg",
                                                              partner_node="extract_agentic",
                                                              handled=[1, 2], deferred=[3])})
    lock = build_step_lock(name="c", kind="deterministic", entrypoint="c:C", step_id="p#c", proto=None,
                           proto_hash=None, interface=None, catalog=CATALOG, static=static(), compiled=info)
    assert compiled_info(parse_model(render_step_lock(lock), StepLock)) == info
    assert compiled_info(lock.model_copy(update={"compiled": None})) is None


M1_STEPS = ["read_pdf", "extract_invoice_fields", "validate_fields", "fix_fields", "save_record",
            "escalate_to_human"]
BASES = {"deterministic": "DeterministicStep", "agentic": "AgenticStep", "shell": "ShellStep"}


@pytest.mark.parametrize("name", M1_STEPS)
def test_m1_locks_and_pyprojects_are_reproduced(name):
    """The hand-written M1 packages follow the compiler's layout: from the proto, the module and the declared
    effects, build_step_lock gives the M1 lock (except compiled/locked_deps) and render_pyproject its marker."""
    pkg = PROCESS / "steps" / name
    m1 = load_step_lock(pkg / "step.lock.yaml")
    p = load_proto_step(PROCESS / "proto" / f"{name}.yaml")
    module, class_name = m1.entrypoint.split(":")
    report = check((pkg / f"{module}.py").read_text(), class_name=class_name, base=BASES[m1.kind],
                   catalog=builtin_catalog(), upstream_nodes=None)
    assert report.errors == []
    lock = build_step_lock(name=name, kind=m1.kind, entrypoint=m1.entrypoint, step_id=local_step_id(PID, name),
                           proto=p, proto_hash=proto_hash(p), interface=m1.interface, static=report,
                           catalog=builtin_catalog(), effects=m1.effects, tier=m1.tier)
    exclude = {"compiled", "locked_deps"}
    assert lock.model_dump(exclude=exclude) == m1.model_dump(exclude=exclude)
    assert render_pyproject(project_name(PID, name), lock.fragment.deps) == (pkg / "pyproject.toml").read_text()


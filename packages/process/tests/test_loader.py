"""Process loading: resolution, phases, interface choice, diagnostics and the code table (PLAN §3.2, §6.2)."""

import re
from pathlib import Path

import pytest

import wynd.process.loader as loader_module
import wynd.process.workspace as workspace_module
from wynd.process.errors import CODES, LoadError, ProcessNotFound
from wynd.process.loader import ResolvedInterface, ResolvedStep
from wynd.process.workspace import CommitTree, load_workspace
from wynd.spec.errors import CODES as SPEC_CODES
from wynd.spec.hashing import proto_hash
from wynd.spec.interface import Interface

READ_PROTO = "processes/intake/proto/read.yaml"


def load(ws: Path, pid: str = "intake"):
    return load_workspace(ws).load_process(pid)


def found(lp) -> list[tuple]:
    return sorted((d.code, d.file, d.loc, d.process) for d in lp.diagnostics)


# --- the basic fixture ------------------------------------------------------------------------------------------------

def test_basic_process_resolves_every_reference_kind(make_repo):
    lp = load(make_repo("basic"))
    assert (lp.id, lp.dir, lp.path) == ("intake", "processes/intake", "processes/intake/process.yaml")
    assert lp.source.file == lp.path
    assert lp.diagnostics == []
    assert list(lp.steps) == ["read", "draft", "guess", "extract", "greet", "file"]
    summary = {
        key: (rs.ref_kind, rs.package and rs.package.id, rs.package and rs.package.dir, rs.child)
        for key, rs in lp.steps.items()
    }
    assert summary == {
        "read": ("local", "intake#read", "processes/intake/steps/read", None),
        "draft": ("local", "intake#draft", "processes/intake/steps/draft", None),
        "guess": ("local", "intake#guess", "processes/intake/steps/guess", None),
        "extract": ("root", "finance:extract/invoice", "teams/finance/steps/extract/invoice", None),
        "greet": ("root", "shared:greet", "shared/steps/greet", None),
        "file": ("process", None, None, "finance/invoices"),
    }
    assert lp.steps["file"] == ResolvedStep("file", "process:finance/invoices", "process", None, "finance/invoices")
    assert list(lp.children) == ["finance/invoices"]
    assert lp.children["finance/invoices"].doc.name == "invoices"


def test_phases_and_interface_choice(make_repo):
    steps = load(make_repo("basic")).steps
    read, draft, guess, extract, greet = (steps[k].package for k in ("read", "draft", "guess", "extract", "greet"))

    assert (read.phase, read.stale, read.interface.source, read.interface.precise) == ("compiled", False, "lock", True)
    assert read.interface.interface == read.lock.interface
    assert read.proto_path == READ_PROTO and read.proto_hash == read.lock.proto_hash == proto_hash(read.proto)

    assert (draft.phase, draft.lock, draft.interface.source, draft.interface.precise) == ("design", None, "proto", True)
    assert draft.interface.interface == draft.proto.interface()

    assert (greet.phase, greet.interface.source) == ("design", "proto")
    assert greet.proto_path == "shared/steps/greet/proto.yaml"

    assert (extract.phase, extract.proto, extract.proto_hash) == ("compiled", None, None)
    assert extract.interface == ResolvedInterface(None, "none", False)

    assert (guess.phase, guess.interface.source, guess.interface.precise) == ("design", "examples", False)
    assert guess.interface.interface == Interface(
        input={"type": "object", "properties": {"text": {}, "lang": {}}, "additionalProperties": False},
        outputs={
            "done": {"type": "object", "properties": {"exit": {"const": "done"}, "kind": {}, "confidence": {}},
                     "required": ["exit"], "additionalProperties": False},
            "unsure": {"type": "object", "properties": {"exit": {"const": "unsure"}, "reason": {}},
                       "required": ["exit"], "additionalProperties": False},
        },
    )


def test_compiled_step_without_snapshot_falls_back_to_its_proto(make_repo):
    lock = "processes/intake/steps/read/step.lock.yaml"
    ws = make_repo("basic")
    text = (ws / lock).read_text()
    ws = make_repo("basic", files={lock: text[:text.index("interface:")]})
    read = load(ws).steps["read"].package
    assert (read.phase, read.lock.interface, read.interface.source) == ("compiled", None, "proto")


def test_edited_proto_makes_the_step_stale(make_repo, commit):
    ws = make_repo("basic")
    committed_before = CommitTree(ws, "HEAD")
    proto = (ws / READ_PROTO).read_text()
    commit(ws, "edit proto", {READ_PROTO: proto.replace("Read the text", "Read all of the text")})
    read = load(ws).steps["read"].package
    assert (read.phase, read.stale, read.interface.source) == ("design", True, "proto")
    assert read.proto_hash != read.lock.proto_hash
    # the commit before the edit still has the compiled step
    old = load_workspace(ws, committed_before).load_process("intake").steps["read"].package
    assert (old.phase, old.stale) == ("compiled", False)


def test_formatting_changes_do_not_make_the_step_stale(make_repo):
    reformatted = """# the same proto, reformatted
examples:
  - outputs: {text: "hello"}
    inputs:
      path: 'docs/a.txt'
outputs: { text: "string" }
inputs: { path: path }   # the document
instruction: "Read the text of the document at path."
name: read
kind: proto_step
"""
    read = load(make_repo("basic", files={READ_PROTO: reformatted})).steps["read"].package
    assert (read.phase, read.stale) == ("compiled", False)


def test_closure_and_memoisation(make_repo):
    workspace = load_workspace(make_repo("basic"))
    lp = workspace.load_process("intake")
    assert workspace.load_process("intake") is lp
    assert lp.children["finance/invoices"] is workspace.load_process("finance/invoices")
    assert list(lp.closure_processes()) == ["intake", "finance/invoices"]
    assert list(lp.closure_packages()) == [
        "intake#read", "intake#draft", "intake#guess", "finance:extract/invoice", "shared:greet",
        "finance/invoices#check",
    ]


def test_unknown_process_is_e127(make_repo):
    workspace = load_workspace(make_repo("basic"))
    with pytest.raises(ProcessNotFound, match="unknown process 'finance'") as err:
        workspace.load_process("finance")
    assert err.value.process == "finance"


def test_dogfood_workspace_loads_cleanly(make_repo, repo_root):
    ws = make_repo(source=repo_root / "examples" / "invoices", subdir="examples/invoices")
    workspace = load_workspace(ws)
    assert workspace.diagnostics == []
    lp = workspace.load_process("process_supplier_invoice")
    assert [d for d in lp.diagnostics if d.severity == "error"] == []
    assert {key: rs.package.id for key, rs in lp.steps.items()} == {
        "read": "process_supplier_invoice#read_pdf",
        "extract": "process_supplier_invoice#extract_invoice_fields",
        "validate": "process_supplier_invoice#validate_fields",
        "fix": "process_supplier_invoice#fix_fields",
        "save": "process_supplier_invoice#save_record",
        "escalate": "process_supplier_invoice#escalate_to_human",
    }
    assert all(rs.package.interface.source in ("lock", "proto") for rs in lp.steps.values())


# --- loader diagnostics ---------------------------------------------------------------------------------------------

def test_process_name_must_match_its_folder(make_repo):
    path = "processes/finance/invoices/process.yaml"
    ws = make_repo("basic")
    ws = make_repo("basic", files={path: (ws / path).read_text().replace("name: invoices", "name: invoice")})
    lp = load(ws, "finance/invoices")
    [diagnostic] = lp.diagnostics
    assert (diagnostic.code, diagnostic.file, diagnostic.loc, diagnostic.line) == ("E113", path, ("name",), 2)
    assert diagnostic.message == "process.yaml name 'invoice' must equal its folder name 'invoices'"
    assert diagnostic.process == "finance/invoices"


@pytest.mark.parametrize(
    ("path", "old", "new", "pid", "expected"),
    [
        (READ_PROTO, "name: read", "name: reader", "intake", "proto-step name 'reader' must equal 'read'"),
        ("shared/steps/greet/proto.yaml", "name: greet", "name: hello", "intake",
         "proto-step name 'hello' must equal 'greet'"),
    ],
)
def test_proto_name_must_match_its_reference(make_repo, path, old, new, pid, expected):
    ws = make_repo("basic")
    ws = make_repo("basic", files={path: (ws / path).read_text().replace(old, new)})
    [diagnostic] = load(ws, pid).diagnostics
    assert (diagnostic.code, diagnostic.file, diagnostic.loc, diagnostic.message) == ("E119", path, ("name",), expected)


def test_incomplete_local_package(make_repo):
    ws = make_repo("basic", files={
        "processes/intake/steps/read/step.lock.yaml": None,                       # proto remains: design phase
        "processes/intake/steps/draft/step.lock.yaml": "wynd: 1\n",               # proto remains
        "processes/intake/steps/lonely/pyproject.toml": "[project]\nname = 'x'\n",  # nothing else
        "processes/intake/process.yaml": (
            "kind: process\nname: intake\nentry: read\nsteps:\n  read: {use: ./steps/read}\n"
            "  draft: {use: ./steps/draft}\n  lonely: {use: ./steps/lonely}\n"
        ),
    })
    lp = load(ws)
    assert found(lp) == [
        ("E123", "processes/intake/steps/draft/step.lock.yaml", (), "intake"),
        ("E123", "processes/intake/steps/lonely/pyproject.toml", (), "intake"),
        ("E123", "processes/intake/steps/read/pyproject.toml", (), "intake"),
    ]
    assert lp.diagnostics[0].message == (
        "step package 'processes/intake/steps/read' has pyproject.toml but not step.lock.yaml"
    )
    read, draft = lp.steps["read"].package, lp.steps["draft"].package
    assert (read.phase, read.lock, read.interface.source) == ("design", None, "proto")
    assert (draft.phase, draft.lock) == ("design", None)
    assert lp.steps["lonely"].package is None


def test_document_errors_are_diagnostics_of_the_referencing_process(make_repo):
    ws = make_repo("basic", files={
        "processes/intake/proto/draft.yaml": "kind: proto_step\nname: draft\ninstruction: x\ninputs: {a: strin}\n",
        "processes/intake/steps/read/step.lock.yaml": "wynd: 1\nname: read\nkind: magic\nentrypoint: read:Read\n",
        "processes/intake/proto/guess.yaml": "kind: proto_step\nname: guess\ninstruction: [unclosed\n",
    })
    lp = load(ws)
    assert {(d.code, d.file) for d in lp.diagnostics} == {
        ("E-TYPE", "processes/intake/proto/draft.yaml"),
        ("E-KIND", "processes/intake/steps/read/step.lock.yaml"),
        ("E-YAML", "processes/intake/proto/guess.yaml"),
    }
    assert all(d.process == "intake" and d.line is not None for d in lp.diagnostics)
    draft, read, guess = (lp.steps[k].package for k in ("draft", "read", "guess"))
    assert (draft.proto, draft.interface.source) == (None, "none")
    assert (read.lock, read.phase, read.interface.source) == (None, "design", "proto")
    assert (guess.proto, guess.proto_path) == (None, "processes/intake/proto/guess.yaml")


def test_proto_document_checks_are_reported(make_repo):
    ws = make_repo("basic", files={
        "processes/intake/proto/draft.yaml": (
            "kind: proto_step\nname: draft\ninstruction: Summarise.\ninputs: {text: string}\n"
            "outputs: {summary: string}\nexamples:\n  - inputs: {text: 3}\n    outputs: {summary: [1]}\n"
        ),
        "shared/steps/greet/proto.yaml": (
            "kind: proto_step\nname: greet\ninstruction: Greet.\ninputs: {person: string}\noutputs: {line: string}\n"
        ),
    })
    assert found(load(ws)) == [
        ("E-EXAMPLE", "processes/intake/proto/draft.yaml", ("examples", 0, "inputs", "text"), "intake"),
        ("E-EXAMPLE", "processes/intake/proto/draft.yaml", ("examples", 0, "outputs", "summary"), "intake"),
        ("W-PROTO-NO-EXAMPLES", "shared/steps/greet/proto.yaml", ("examples",), "intake"),
    ]


def test_examples_of_undeclared_sections_are_not_checked_against_empty_schemas(make_repo):
    # guess declares neither inputs nor outputs (clean); this one declares inputs only, so inputs are still checked.
    ws = make_repo("basic", files={
        "processes/intake/proto/draft.yaml": (
            "kind: proto_step\nname: draft\ninstruction: Summarise.\ninputs: {text: string}\n"
            "examples:\n  - inputs: {text: hi, extra: 1}\n    outputs: {summary: hi}\n  - inputs: {}\n"
        ),
    })
    lp = load(ws)
    assert found(lp) == [
        ("E-EXAMPLE", "processes/intake/proto/draft.yaml", ("examples", 0, "inputs", "extra"), "intake"),
        ("E-EXAMPLE", "processes/intake/proto/draft.yaml", ("examples", 1, "inputs", "text"), "intake"),
    ]
    draft = lp.steps["draft"].package
    assert draft.interface.source == "examples"
    assert list(draft.interface.interface.outputs["done"]["properties"]) == ["exit", "summary"]


def test_invalid_process_yaml_raises_load_error(make_repo):
    ws = make_repo("basic", files={"processes/finance/invoices/process.yaml": "kind: process\nname: [\n"})
    workspace = load_workspace(ws)
    for pid in ("finance/invoices", "intake"):  # a broken child fails its parent's load too
        with pytest.raises(LoadError) as err:
            workspace.load_process(pid)
        [diagnostic] = err.value.diagnostics
        assert (diagnostic.code, diagnostic.file, diagnostic.process) == (
            "E-YAML", "processes/finance/invoices/process.yaml", "finance/invoices"
        )


def test_process_level_layout_diagnostics_reach_the_process(make_repo):
    ws = make_repo("basic", files={
        "processes/intake/sub/process.yaml": "kind: process\nname: sub\nentry: s\nsteps:\n  s: {use: ./steps/s}\n",
        "processes/intake/sub/proto/s.yaml": "kind: proto_step\nname: s\ninstruction: S.\nexamples: [{}]\n",
        "processes/intake/misplaced/step.lock.yaml": "wynd: 1\n",
    })
    workspace = load_workspace(ws)
    assert found(workspace.load_process("intake/sub")) == [("E110", "processes/intake/sub/process.yaml", (),
                                                           "intake/sub")]
    assert found(workspace.load_process("intake")) == [("E115", "processes/intake/misplaced/step.lock.yaml", (),
                                                       "intake")]


# --- the code table -------------------------------------------------------------------------------------------------

PLAN_PROCESS_CODES = (
    [f"E{n}" for n in (*range(100, 107), *range(110, 117), *range(119, 126), 127, 128)]
    + ["W128", "W129", "I130", "E204", "E208", "E209", "E210", "E211", "E212", "E215", "E219", "E223", "I201", "W202"]
    + [f"W{n}" for n in range(203, 208)]
)


def test_code_table_is_the_plan_namespace():
    assert sorted(CODES) == sorted(PLAN_PROCESS_CODES)
    assert not set(CODES) & SPEC_CODES
    for code, (severity, template) in CODES.items():
        assert severity == {"E": "error", "W": "warning", "I": "info"}[code[0]]
        assert template.strip() == template and template


def test_every_emitted_code_is_listed():
    emitted = set()
    for module in (workspace_module, loader_module):
        source = Path(module.__file__).read_text()
        emitted |= set(re.findall(r'diagnostic\(\s*"([A-Z][\w-]*)"', source))
    assert emitted and emitted <= set(CODES)
    assert emitted == {
        "E100", "E101", "E103", "E110", "E111", "E112", "E113", "E114", "E115", "E116", "E119", "E121", "E122",
        "E123", "E124", "E125",
    }

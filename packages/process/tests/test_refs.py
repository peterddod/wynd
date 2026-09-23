"""`use:` references: the three forms, resolution failures and process-reference cycles (PLAN §3.1, §3.3, §6.2)."""

import pytest

from wynd.process.errors import LoadError
from wynd.process.workspace import load_workspace
from wynd.spec.workspace import step_module_name

WYND_YAML = "process_roots: [processes]\nstep_roots:\n  shared: shared/steps\n  finance: teams/finance/steps\n"
PROTO = "kind: proto_step\nname: {name}\ninstruction: Do it.\ninputs: {{x: string}}\noutputs: {{y: string}}\n" \
        "examples:\n  - inputs: {{x: a}}\n    outputs: {{y: b}}\n"


def workspace_with(make_repo, use: str, extra: dict | None = None):
    files = {
        "wynd.yaml": WYND_YAML,
        "processes/p/process.yaml": f"kind: process\nname: p\nentry: s\nsteps:\n  s: {{use: '{use}'}}\n",
        "processes/p/proto/local.yaml": PROTO.format(name="local"),
        "processes/child/process.yaml": "kind: process\nname: child\nentry: c\nsteps:\n  c: {use: ./steps/c}\n",
        "processes/child/proto/c.yaml": PROTO.format(name="c"),
        "processes/team/sub/process.yaml": "kind: process\nname: sub\nentry: c\nsteps:\n  c: {use: ./steps/c}\n",
        "processes/team/sub/proto/c.yaml": PROTO.format(name="c"),
        "shared/steps/tidy/proto.yaml": PROTO.format(name="tidy"),
        "teams/finance/steps/extract/invoice/proto.yaml": PROTO.format(name="invoice"),
        **(extra or {}),
    }
    return load_workspace(make_repo(files=files))


@pytest.mark.parametrize(
    ("use", "kind", "step_id", "package_dir", "child"),
    [
        ("./steps/local", "local", "p#local", "processes/p/steps/local", None),
        ("shared:tidy", "root", "shared:tidy", "shared/steps/tidy", None),
        ("finance:extract/invoice", "root", "finance:extract/invoice", "teams/finance/steps/extract/invoice", None),
        ("process:child", "process", None, None, "child"),
        ("process:team/sub", "process", None, None, "team/sub"),
    ],
)
def test_the_three_use_forms(make_repo, use, kind, step_id, package_dir, child):
    lp = workspace_with(make_repo, use).load_process("p")
    assert lp.diagnostics == []
    step = lp.steps["s"]
    assert (step.use, step.ref_kind, step.child) == (use, kind, child)
    if kind == "process":
        assert step.package is None
        assert list(lp.children) == [child]
    else:
        assert (step.package.id, step.package.dir) == (step_id, package_dir)
        assert lp.children == {}


def test_proto_locations(make_repo):
    workspace = workspace_with(make_repo, "./steps/local")
    assert workspace.load_process("p").steps["s"].package.proto_path == "processes/p/proto/local.yaml"
    workspace = workspace_with(make_repo, "finance:extract/invoice")
    package = workspace.load_process("p").steps["s"].package
    assert package.proto_path == "teams/finance/steps/extract/invoice/proto.yaml"


def test_same_named_packages_get_distinct_ids_and_modules(make_repo):
    workspace = workspace_with(make_repo, "./steps/local", {
        "processes/q/process.yaml": "kind: process\nname: q\nentry: s\nsteps:\n  s: {use: ./steps/local}\n",
        "processes/q/proto/local.yaml": PROTO.format(name="local"),
    })
    ids = [workspace.load_process(pid).steps["s"].package.id for pid in ("p", "q")]
    assert ids == ["p#local", "q#local"]
    assert step_module_name(ids[0]) != step_module_name(ids[1])


@pytest.mark.parametrize(
    ("use", "hint"),
    [
        ("../other/steps/x", "relative traversal ('..') is not permitted"),
        ("./steps/../x", "relative traversal ('..') is not permitted"),
        ("shared:tidy/../x", "relative traversal ('..') is not permitted"),
        ("process:../child", "relative traversal ('..') is not permitted"),
        ("/abs/steps/x", "absolute paths are not permitted"),
        ("~/steps/x", "absolute paths are not permitted"),
        ("C:/steps/x", "absolute paths are not permitted"),
        ("shared:tidy\\x", "use forward slashes"),
        ("./steps/a/b", "process-local steps are ./steps/<name> (a single segment)"),
        ("./proto/local", "process-local steps are ./steps/<name> (a single segment)"),
        ("./steps/", "process-local steps are ./steps/<name> (a single segment)"),
        ("process:", "invalid process id"),
        ("process:a//b", "invalid process id"),
        ("steps/local", None),
        ("Shared:tidy", None),
        ("shared:", None),
        ("process", None),
        ("", None),
    ],
)
def test_invalid_use_fails_the_load_with_the_three_forms(make_repo, use, hint):
    workspace = workspace_with(make_repo, use)
    with pytest.raises(LoadError) as err:
        workspace.load_process("p")
    [diagnostic] = err.value.diagnostics
    assert (diagnostic.code, diagnostic.file, diagnostic.loc, diagnostic.process) == (
        "E-USE", "processes/p/process.yaml", ("steps", "s", "use"), "p"
    )
    assert "./steps/<name>, <alias>:<path>, process:<id>" in diagnostic.message
    assert diagnostic.message.endswith(f"; {hint}") if hint else ";" not in diagnostic.message


@pytest.mark.parametrize(
    ("use", "code", "message"),
    [
        ("vendor:x", "E121", "step 's': unknown step root alias 'vendor' (configured: finance, shared)"),
        ("./steps/missing", "E122", "step 's': './steps/missing' does not resolve to a step package or proto-step "
                                    "(looked for processes/p/steps/missing and processes/p/proto/missing.yaml)"),
        ("shared:nope/deeper", "E122", "step 's': 'shared:nope/deeper' does not resolve to a step package or "
                                       "proto-step (looked for shared/steps/nope/deeper and "
                                       "shared/steps/nope/deeper/proto.yaml)"),
        ("process:nope", "E124", "step 's': process 'nope' not found under any process root"),
    ],
)
def test_unresolved_references(make_repo, use, code, message):
    lp = workspace_with(make_repo, use).load_process("p")
    [diagnostic] = lp.diagnostics
    assert (diagnostic.code, diagnostic.message) == (code, message)
    assert (diagnostic.file, diagnostic.loc, diagnostic.line, diagnostic.process) == (
        "processes/p/process.yaml", ("steps", "s", "use"), 5, "p"
    )
    step = lp.steps["s"]
    assert step.package is None and lp.children == {}


def test_unknown_alias_with_no_step_roots(make_repo):
    lp = load_workspace(make_repo(files={
        "wynd.yaml": "process_roots: [processes]\n",
        "processes/p/process.yaml": "kind: process\nname: p\nentry: s\nsteps:\n  s: {use: 'shared:x'}\n",
    })).load_process("p")
    assert [d.message for d in lp.diagnostics] == ["step 's': unknown step root alias 'shared' (configured: none)"]


# --- process-reference cycles -------------------------------------------------------------------------------------

def test_three_cycle_is_reported_once_on_the_closing_reference(make_repo):
    workspace = load_workspace(make_repo("graph"))
    a = workspace.load_process("cycle/a")
    b = a.children["cycle/b"]
    c = b.children["cycle/c"]
    assert (a.diagnostics, b.diagnostics) == ([], [])
    [cycle] = c.diagnostics
    assert (cycle.code, cycle.file, cycle.loc, cycle.process) == (
        "E125", "processes/cycle/c/process.yaml", ("steps", "next", "use"), "cycle/c"
    )
    assert cycle.message == "process reference cycle: cycle/a -> cycle/b -> cycle/c -> cycle/a"
    assert c.children == {} and c.steps["next"].child == "cycle/a"
    assert list(a.closure_processes()) == ["cycle/a", "cycle/b", "cycle/c"]


def test_cycle_is_reported_where_the_load_closes_it(make_repo):
    workspace = load_workspace(make_repo("graph"))
    b = workspace.load_process("cycle/b")
    a = b.children["cycle/c"].children["cycle/a"]
    [cycle] = a.diagnostics
    assert cycle.message == "process reference cycle: cycle/b -> cycle/c -> cycle/a -> cycle/b"


def test_self_reference_is_a_cycle(make_repo):
    lp = load_workspace(make_repo("graph")).load_process("selfref")
    [cycle] = lp.diagnostics
    assert (cycle.code, cycle.message) == ("E125", "process reference cycle: selfref -> selfref")
    assert lp.children == {}


def test_diamond_loads_the_shared_child_once(make_repo):
    workspace = load_workspace(make_repo("graph"))
    top = workspace.load_process("diamond/top")
    left, right = top.children["diamond/left"], top.children["diamond/right"]
    assert left.children["diamond/bottom"] is right.children["diamond/bottom"]
    assert left.children["diamond/bottom"] is workspace.load_process("diamond/bottom")
    assert all(lp.diagnostics == [] for lp in top.closure_processes().values())
    assert list(top.closure_processes()) == ["diamond/top", "diamond/left", "diamond/bottom", "diamond/right"]
    assert list(top.closure_packages()) == ["diamond/bottom#work"]

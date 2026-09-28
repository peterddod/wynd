"""Reference closure (PLAN §3.19, SPEC §11): wynd.yaml + the process dir + every root-step package dir it references +
each child's closure, transitively; workspace-relative, sorted, ancestors only."""

from support.proc_git_harness import CLOSURES, WORKSPACE, process_yaml, proto_yaml

from wynd.process.git import reference_closure
from wynd.process.workspace import CommitTree, load_workspace


def test_closure_of_local_root_step_child_and_diamond_processes(make_repo):
    ws = load_workspace(make_repo(files=WORKSPACE))
    assert {pid: reference_closure(ws, pid) for pid in CLOSURES} == CLOSURES
    assert "shared/steps/unused" not in {p for closure in CLOSURES.values() for p in closure}


def test_closure_of_the_basic_fixture_includes_nested_root_steps_and_the_child(make_repo):
    ws = load_workspace(make_repo("basic"))
    assert reference_closure(ws, "intake") == [
        "processes/finance/invoices",
        "processes/intake",
        "shared/steps/greet",
        "teams/finance/steps/extract/invoice",
        "wynd.yaml",
    ]
    assert reference_closure(ws, "finance/invoices") == ["processes/finance/invoices", "wynd.yaml"]


def test_dogfood_closure(make_repo, repo_root):
    ws = make_repo(source=repo_root / "examples" / "invoices", subdir="examples/invoices")
    assert reference_closure(load_workspace(ws), "process_supplier_invoice") == [
        "processes/process_supplier_invoice",
        "wynd.yaml",
    ]


def test_closure_is_workspace_relative_in_a_subdirectory_workspace(make_repo):
    ws = make_repo(files=WORKSPACE, subdir="nested/ws")
    assert reference_closure(load_workspace(ws), "beta") == CLOSURES["beta"]


def test_closure_follows_the_tree_it_is_computed_from(make_repo, commit):
    ws = make_repo(files=WORKSPACE)
    commit(ws, "beta drops its child", {
        "processes/beta/process.yaml": process_yaml("beta", {"own": "./steps/own", "fetch": "shared:net/fetch"}),
    })
    assert reference_closure(load_workspace(ws), "beta") == ["processes/beta", "shared/steps/net/fetch", "wynd.yaml"]
    assert reference_closure(load_workspace(ws, CommitTree(ws, "HEAD~1")), "beta") == CLOSURES["beta"]


def test_unresolved_references_contribute_nothing(make_repo):
    ws = make_repo(files={
        **WORKSPACE,
        "processes/eps/process.yaml": process_yaml("eps", {
            "own": "./steps/own", "gone": "shared:missing", "alias": "nowhere:x", "child": "process:no/such",
            "tool": "shared:util",
        }),
        "processes/eps/proto/own.yaml": proto_yaml("own"),
    })
    assert reference_closure(load_workspace(ws), "eps") == ["processes/eps", "shared/steps/util", "wynd.yaml"]

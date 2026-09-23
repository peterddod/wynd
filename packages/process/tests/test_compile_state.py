"""Compile state (PLAN §6.1, §15 item 62, SPEC §11 "design"): steps not compiled or stale, children in design and
stale or missing edge-lock entries make a process design; status reads it per commit through a `CommitTree`."""

from support.proc_git_harness import compiled_step, process_yaml, proto_yaml

from wynd.process.compile import CompileState, compile_state
from wynd.process.hashing import process_hash, step_hash
from wynd.process.workspace import CommitTree, load_workspace
from wynd.spec.lockfiles import check_hash

WYND_YAML = "process_roots: [processes]\nstep_roots:\n  shared: shared/steps\n"
LOCKED = "wynd: 1\nname: tool\nkind: deterministic\nentrypoint: tool:Tool\n"


def compiled_workspace() -> dict[str, str]:
    """`main` (two compiled local steps, a hand-written root step, child `sub`) and `sub` (one compiled step)."""
    return {
        "wynd.yaml": WYND_YAML,
        "processes/main/process.yaml": process_yaml(
            "main", {"read": "./steps/read", "write": "./steps/write", "tool": "shared:tool", "sub": "process:sub"}
        ),
        **compiled_step("processes/main", "read"),
        **compiled_step("processes/main", "write"),
        "shared/steps/tool/pyproject.toml": '[project]\nname = "tool"\nversion = "0.1.0"\n',
        "shared/steps/tool/step.lock.yaml": LOCKED,
        "shared/steps/tool/tool.py": "",
        "processes/sub/process.yaml": process_yaml("sub", {"calc": "./steps/calc"}),
        **compiled_step("processes/sub", "calc"),
    }


def state(ws, pid="main", tree=None) -> CompileState:
    return compile_state(load_workspace(ws, tree), pid)


def test_a_fully_compiled_process_is_not_design(make_repo):
    ws = make_repo(files=compiled_workspace())
    workspace = load_workspace(ws)
    cs = compile_state(workspace, "main")
    assert (cs.design, cs.reasons) == (False, [])
    assert cs.process_hash == process_hash(workspace.tree, workspace.load_process("main"))
    by_name = {s.name: s for s in cs.steps}
    assert list(by_name) == ["read", "write", "tool", "sub"]
    read = by_name["read"]
    package = workspace.load_process("main").steps["read"].package
    assert (read.use, read.step_id, read.compiled) == ("./steps/read", "main#read", True)
    assert read.proto_hash == read.locked_proto_hash == package.proto_hash
    assert read.step_hash == step_hash(workspace.tree, package)
    tool = by_name["tool"]                                   # hand-written, proto-less
    assert (tool.step_id, tool.compiled, tool.proto_hash, tool.locked_proto_hash) == ("shared:tool", True, None, None)
    sub = by_name["sub"]
    assert (sub.use, sub.step_id, sub.compiled, sub.step_hash) == ("process:sub", None, True, None)


def test_proto_only_and_stale_steps_make_the_process_design(make_repo, write_files):
    ws = make_repo(files={
        **compiled_workspace(),
        "processes/main/proto/draft.yaml": proto_yaml("draft"),
        "processes/main/process.yaml": process_yaml(
            "main", {"read": "./steps/read", "write": "./steps/write", "draft": "./steps/draft"}
        ),
    })
    write_files(ws, {"processes/main/proto/write.yaml": proto_yaml("write", "The instruction changed.")})
    cs = state(ws)
    assert cs.design
    assert cs.reasons == ["step write: proto-step changed since compile", "step draft: proto-step not compiled"]
    steps = {s.name: s for s in cs.steps}
    assert steps["read"].compiled and not steps["write"].compiled and not steps["draft"].compiled
    assert steps["write"].proto_hash != steps["write"].locked_proto_hash is not None
    assert steps["write"].step_hash is not None
    assert (steps["draft"].locked_proto_hash, steps["draft"].step_hash) == (None, None)


def test_a_child_in_design_makes_the_parent_design(make_repo, write_files):
    ws = make_repo(files=compiled_workspace())
    write_files(ws, {"processes/sub/proto/calc.yaml": proto_yaml("calc", "Changed.")})
    assert state(ws, "sub").reasons == ["step calc: proto-step changed since compile"]
    cs = state(ws)
    assert (cs.design, cs.reasons) == (True, ["child sub is in design"])
    assert not {s.name: s for s in cs.steps}["sub"].compiled


def test_unresolved_references_are_design(make_repo):
    files = compiled_workspace()
    files["processes/main/process.yaml"] = process_yaml(
        "main", {"read": "./steps/read", "gone": "shared:missing", "child": "process:no/such"}
    )
    cs = state(make_repo(files=files))
    assert cs.reasons == [
        "step gone: 'shared:missing' does not resolve to a step package or proto-step",
        "step child: process 'no/such' could not be loaded",
    ]


AGENTIC_EDGES = """edges:
  - from: read.done
    kind: agentic
    to:
      - step: write
        name: save
        check: The document is a final invoice.
      - step: write
        check: The document is a receipt.
        context: [steps.read.outputs]
      - step: sub
      - step: write
        check: Unreachable after the else.
  - from: write.done
    to: sub
"""


def edge_lock(entries: dict[str, str]) -> str:
    return "wynd: 1\nedges:\n" + "".join(f"  {key}:\n    check_hash: '{h}'\n" for key, h in entries.items())


def agentic_workspace(lock: str | None) -> dict[str, str]:
    files = compiled_workspace()
    files["processes/main/process.yaml"] = process_yaml(
        "main", {"read": "./steps/read", "write": "./steps/write", "sub": "process:sub"}, AGENTIC_EDGES
    )
    if lock is not None:
        files["processes/main/edges.lock.yaml"] = lock
    return files


SAVE = check_hash("The document is a final invoice.", None)
RECEIPT = check_hash("The document is a receipt.", ["steps.read.outputs"])


def test_locked_agentic_branches_are_compiled(make_repo):
    cs = state(make_repo(files=agentic_workspace(edge_lock({"read.done[save]": SAVE, "read.done[1]": RECEIPT}))))
    assert (cs.design, cs.reasons) == (False, [])


def test_missing_or_stale_edge_lock_entries_make_the_process_design(make_repo):
    cs = state(make_repo(files=agentic_workspace(None)))
    assert cs.reasons == [
        "agentic branch read.done[save]: no edges.lock.yaml entry",
        "agentic branch read.done[1]: no edges.lock.yaml entry",
    ]
    stale = check_hash("The document is a receipt.", None)                  # locked before `context` was added
    cs = state(make_repo(files=agentic_workspace(edge_lock({"read.done[save]": SAVE, "read.done[1]": stale}))))
    assert (cs.design, cs.reasons) == (True, ["agentic branch read.done[1]: check changed since it was locked"])


def test_an_invalid_edge_lock_is_design(make_repo):
    cs = state(make_repo(files=agentic_workspace("wynd: 1\nedges: [not, a, mapping]\n")))
    assert cs.design and cs.reasons[0].startswith("processes/main/edges.lock.yaml is invalid: ")


def test_state_is_per_commit(make_repo, commit):
    ws = make_repo(files=compiled_workspace())
    compiled_at = commit(ws, "noop", {"README.md": "x\n"})
    edited = commit(ws, "design edit", {"processes/main/proto/read.yaml": proto_yaml("read", "Changed.")})
    assert state(ws, tree=CommitTree(ws, compiled_at)).design is False
    assert state(ws, tree=CommitTree(ws, edited)).design is True
    assert state(ws).design is True
    assert (state(ws, tree=CommitTree(ws, compiled_at)).process_hash
            != state(ws, tree=CommitTree(ws, edited)).process_hash)

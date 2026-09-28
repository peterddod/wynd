"""Derived status (PLAN §8.1 status row, §15 item 62; `$DRAFTS/06 §5.5`): every flag is read at the process HEAD
through a `CommitTree`; working-tree dirt only shows in `dirty`."""

from __future__ import annotations

from datetime import UTC, datetime

from wynd.process.artefacts import BuildInfo
from wynd.process.compile import compile_state
from wynd.process.workspace import CommitTree, load_workspace
from wynd.runtime.storage.models import TestResult
from wynd.spec.env_manifest import EnvManifest

UPPER_PROTO = "processes/p1/proto/upper.yaml"


def head(git, ws) -> str:
    return git(ws, "rev-parse", "HEAD").strip()


def record_tests(controller, pid: str, commit: str, *, passed: bool) -> None:
    ws_at = load_workspace(controller.ctx.root, CommitTree(controller.ctx.root, commit))
    key = f"process:{pid}:{compile_state(ws_at, pid).process_hash}"
    controller.ctx.stores.runs.put_test_result(TestResult(
        commit=commit, key=key, passed=passed, counts={"passed": 3}, ran_at=datetime(2026, 9, 22, tzinfo=UTC)))


def put_build(controller, pid: str, commit: str) -> None:
    staged = controller.ctx.state_dir / "tmp" / f"stage-{commit[:7]}"
    staged.mkdir(parents=True)
    controller.ctx.artefacts.put_build(staged, BuildInfo(
        process=pid, commit=commit, source_sha=commit, job_id=None, process_hash="sha256:" + "0" * 64, image="img",
        image_id=None, image_digest=None, base={}, manifest=EnvManifest(process=pid, vars=[]),
        created_at=datetime(2026, 9, 22, tzinfo=UTC), dir=""))


def put_release(controller, id: str, pid: str, commit: str, *, enabled: bool = True, **extra) -> None:
    controller.ctx.docs.put("releases", id, {"id": id, "process_id": pid, "commit": commit, "image": "img",
                                             "trigger": {"kind": "manual"}, "env": {}, "enabled": enabled, **extra})


def edit_upper_proto(ws) -> dict[str, str]:
    text = (ws / UPPER_PROTO).read_text().replace("blank text takes", "whitespace-only text takes")
    return {UPPER_PROTO: text}


def test_a_clean_compiled_process_without_test_results(controller, workspace, git):
    status = controller.processes.status("p1")
    assert status.head.commit == head(git, workspace)
    assert (status.head.short, status.head.subject) == (head(git, workspace)[:7], "initial")
    assert (status.design, status.compiled, status.built, status.released) == (False, False, False, False)
    assert (status.tests, status.design_steps, status.releases, status.dirty) == ("unknown", [], [], [])


def test_compiled_needs_a_passing_result_recorded_at_the_process_head(controller, workspace, git):
    record_tests(controller, "p1", head(git, workspace), passed=False)
    status = controller.processes.status("p1")
    assert (status.tests, status.compiled) == ("failed", False)
    record_tests(controller, "p1", head(git, workspace), passed=True)
    status = controller.processes.status("p1")
    assert (status.tests, status.compiled) == ("passed", True)


def test_design_from_an_uncompiled_proto_and_from_a_proto_change(controller, workspace, commit):
    p2 = controller.processes.status("p2")
    assert (p2.design, p2.compiled, p2.design_steps) == (True, False, ["tag"])

    sha = commit(workspace, "edit upper proto", edit_upper_proto(workspace))
    p1 = controller.processes.status("p1")
    assert p1.head.commit == sha and p1.head.subject == "edit upper proto"
    assert (p1.design, p1.design_steps, p1.compiled) == (True, ["upper"], False)


def test_an_unrelated_commit_leaves_head_and_flags_unchanged(controller, workspace, git, commit):
    first = head(git, workspace)
    record_tests(controller, "p1", first, passed=True)
    commit(workspace, "touch p2", {"processes/p2/notes.md": "unrelated\n"})
    status = controller.processes.status("p1")
    assert status.head.commit == first
    assert (status.compiled, status.tests) == (True, "passed")


def test_a_child_edit_moves_the_parent_head_and_makes_it_design(controller, workspace, commit):
    sha = commit(workspace, "edit child proto", edit_upper_proto(workspace))
    parent = controller.processes.status("parent")
    assert parent.head.commit == sha
    assert (parent.design, parent.design_steps) == (True, ["child"])


def test_built_comes_from_the_artefact_store_at_the_process_head(controller, workspace, git, commit):
    put_build(controller, "p1", head(git, workspace))
    assert controller.processes.status("p1").built is True
    commit(workspace, "touch p1", {"processes/p1/notes.md": "x\n"})
    assert controller.processes.status("p1").built is False


def test_released_with_behind_counts(controller, workspace, git, commit):
    first = head(git, workspace)
    put_release(controller, "rel_2", "p1", first)
    put_release(controller, "rel_1", "p1", first, enabled=False)
    put_release(controller, "rel_3", "p2", first)
    commit(workspace, "touch p2", {"processes/p2/notes.md": "x\n"})
    commit(workspace, "touch p1", {"processes/p1/notes.md": "x\n"})
    commit(workspace, "touch p1 again", {"processes/p1/notes.md": "y\n"})
    status = controller.processes.status("p1")
    assert status.released is True
    assert [r.model_dump() for r in status.releases] == [
        {"id": "rel_2", "commit": first, "short": first[:7], "behind": 2, "trigger": "manual", "state": "serving"},
        {"id": "rel_1", "commit": first, "short": first[:7], "behind": 2, "trigger": "manual", "state": "stopped"},
    ]
    put_release(controller, "rel_2", "p1", first, enabled=False, state="error")
    status = controller.processes.status("p1")
    assert status.released is False and status.releases[0].state == "error"


def test_working_tree_dirt_is_reported_but_never_changes_a_flag(controller, workspace, git, write_files):
    record_tests(controller, "p1", head(git, workspace), passed=True)
    process = (workspace / "processes/p1/process.yaml").read_text()
    write_files(workspace, {
        # uncommitted: a new design-phase step in process.yaml, a changed proto, an untracked file
        "processes/p1/process.yaml": process.replace("steps:\n", "steps:\n  extra: { use: ./steps/extra }\n"),
        "processes/p1/proto/extra.yaml": "kind: proto_step\nname: extra\ninstruction: Extra.\n",
        **edit_upper_proto(workspace),
        "processes/p1/new.txt": "untracked\n",
        "processes/p2/notes.md": "outside the closure\n",
    })
    status = controller.processes.status("p1")
    assert (status.design, status.compiled, status.tests, status.design_steps) == (False, True, "passed", [])
    dirty = ["processes/p1/new.txt", "processes/p1/process.yaml", "processes/p1/proto/extra.yaml", UPPER_PROTO]
    assert status.dirty == dirty
    assert controller.processes.status("parent").dirty == dirty
    assert controller.processes.steps("p1")["extra"].phase == "design"         # the working tree does see it


def test_status_of_a_workspace_in_a_repository_subdirectory(subdir_workspace, make_controller, git, write_files):
    controller = make_controller(subdir_workspace)
    assert controller.ctx.subdir == "examples/ws"
    write_files(subdir_workspace, {"processes/p1/new.txt": "x\n"})
    status = controller.processes.status("p1")
    assert status.head.commit == head(git, subdir_workspace)
    assert (status.design, status.dirty) == (False, ["processes/p1/new.txt"])
    assert controller.processes.status("p2").design_steps == ["tag"]

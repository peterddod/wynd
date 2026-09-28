"""`DesignService` (PLAN §8.1 design row; `$DRAFTS/06 §5.7`, `$DRAFTS/07 §12.3`) on the `ws_basic` workspace."""

from __future__ import annotations

import hashlib

import pytest
import yaml

from wynd.controller.api.models_web import SaveRequest
from wynd.controller.errors import DesignLocked, Invalid, NotFound, OutOfScope, RevisionConflict
from wynd.controller.models import ProcessInterface

P1_YAML = "processes/p1/process.yaml"
P1_UPPER = "processes/p1/proto/upper.yaml"
P1_COUNT = "processes/p1/proto/count.yaml"
P2_YAML = "processes/p2/process.yaml"
SHARED_PROTO = "shared/steps/normalise/proto.yaml"


def revision(ws, path: str) -> str:
    return "sha256:" + hashlib.sha256((ws / path).read_bytes()).hexdigest()


def load(ws, path: str) -> dict:
    return yaml.safe_load((ws / path).read_text())


def request(*writes: dict, commit: dict | None = None) -> SaveRequest:
    return SaveRequest.model_validate({"writes": list(writes), "commit": commit})


def write(ws, path: str, doc: dict | None = None, **fields) -> dict:
    """A write of `doc` (default: the file's current document) against the file's current revision."""
    base = revision(ws, path) if (ws / path).exists() else None
    return {"path": path, "base_revision": base, "doc": doc if doc is not None else load(ws, path), **fields}


def committed_paths(git, ws, rev: str = "HEAD") -> list[str]:
    return sorted(git(ws, "show", "--name-only", "--format=", rev).split())


def status_lines(git, ws) -> list[str]:
    return sorted(line for line in git(ws, "status", "--porcelain=v1", "--untracked-files=all").splitlines() if line)


# --- get and scope --------------------------------------------------------------------------------------------------

def test_get_serves_the_design_documents(controller, workspace, git):
    design = controller.design.get("p1")
    assert design.process_id == "p1" and design.head == git(workspace, "rev-parse", "HEAD").strip()
    pf = design.process_file
    assert (pf.path, pf.revision, pf.parse_error) == (P1_YAML, revision(workspace, P1_YAML), None)
    assert pf.yaml == (workspace / P1_YAML).read_text()
    assert pf.doc["steps"] == {"upper": {"use": "./steps/upper"}, "count": {"use": "./steps/count"}}
    assert pf.doc["examples"][1]["inputs"] == {"text": "   "}
    assert sorted(design.protos) == [P1_COUNT, P1_UPPER]
    upper = design.protos[P1_UPPER]
    assert (upper.path, upper.revision) == (P1_UPPER, revision(workspace, P1_UPPER))
    assert upper.doc["exits"] == ["done", "empty"] and upper.yaml == (workspace / P1_UPPER).read_text()
    assert sorted(design.steps) == ["count", "upper"] and design.steps["upper"].phase == "compiled"
    assert design.interface.inputs["required"] == ["text"] and sorted(design.interface.outputs) == ["done", "empty"]
    assert design.validation.model_dump() == {"ok": True, "issues": []}
    assert design.conventions.model_dump() == {"local_use": "./steps/{name}",
                                               "local_proto_path": "processes/p1/proto/{name}.yaml"}
    assert design.available_local == [] and design.locked_by is None


def test_get_includes_referenced_step_root_protos(controller):
    design = controller.design.get("p2")
    assert sorted(design.protos) == ["processes/p2/proto/tag.yaml", SHARED_PROTO]
    assert design.protos[SHARED_PROTO].doc["name"] == "normalise"
    assert (design.steps["normalise"].ref_kind, design.steps["tag"].phase) == ("root", "design")
    assert controller.design.get("parent").protos == {}


def test_get_lists_local_steps_the_process_does_not_reference(controller, workspace, write_files):
    lock = (workspace / "processes/p1/steps/upper/step.lock.yaml").read_text()
    pyproject = (workspace / "processes/p1/steps/upper/pyproject.toml").read_text()
    doc = load(workspace, P1_YAML)
    del doc["steps"]["count"]
    write_files(workspace, {
        P1_YAML: yaml.safe_dump(doc, sort_keys=False),
        "processes/p1/proto/extra.yaml": "kind: proto_step\nname: extra\ninstruction: Do more.\n",
        "processes/p1/steps/old/step.lock.yaml": lock.replace("name: upper", "name: old"),
        "processes/p1/steps/old/pyproject.toml": pyproject,
        "processes/p1/proto/notes.txt": "not a proto",
    })
    available = [step.model_dump() for step in controller.design.get("p1").available_local]
    assert available == [
        {"use": "./steps/count", "proto_path": P1_COUNT, "source_path": "processes/p1/steps/count",
         "phase": "compiled"},
        {"use": "./steps/extra", "proto_path": "processes/p1/proto/extra.yaml", "source_path": None,
         "phase": "design"},
        {"use": "./steps/old", "proto_path": None, "source_path": "processes/p1/steps/old", "phase": "handwritten"},
    ]


def test_get_reports_a_process_file_that_does_not_parse(controller, workspace, write_files):
    write_files(workspace, {P1_YAML: "kind: process\nname: [unclosed\n"})
    design = controller.design.get("p1")
    assert design.process_file.doc is None and design.process_file.yaml == "kind: process\nname: [unclosed\n"
    assert design.process_file.parse_error.line is not None
    assert (design.steps, design.interface) == ({}, ProcessInterface())
    assert not design.validation.ok and design.validation.issues[0].code.startswith("E-YAML")
    assert sorted(design.protos) == [P1_COUNT, P1_UPPER]                     # local protos stay editable


def test_scope(controller):
    assert controller.design.scope("p1") == [P1_YAML, P1_COUNT, P1_UPPER]
    assert controller.design.scope("p2") == [P2_YAML, "processes/p2/proto/tag.yaml", SHARED_PROTO]
    assert controller.design.scope("parent") == ["processes/parent/process.yaml"]
    with pytest.raises(NotFound, match="unknown process 'nope'"):
        controller.design.scope("nope")
    with pytest.raises(NotFound):
        controller.design.get("nope")


# --- save -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    P2_YAML,                                                   # another process
    "processes/p2/proto/tag.yaml",
    "processes/p1/steps/upper/step.lock.yaml",                 # compiled source
    "processes/p1/steps/upper/upper.py",
    SHARED_PROTO,                                              # a step-root proto p1 does not reference
    "processes/p1/proto/../../p2/process.yaml",
    "processes/p1/proto/nested/x.yaml",
    "processes/p1/proto/notes.txt",
    "wynd.yaml",
    "/etc/passwd",
])
def test_save_refuses_paths_outside_the_scope(controller, workspace, path):
    before = (workspace / P1_YAML).read_text()
    doc = load(workspace, P1_YAML) | {"goal": "changed"}
    req = request(write(workspace, P1_YAML, doc), {"path": path, "base_revision": None, "doc": {"kind": "x"}})
    with pytest.raises(OutOfScope) as err:
        controller.design.save("p1", req)
    assert err.value.http == 403 and err.value.code == "out_of_scope"
    assert (workspace / P1_YAML).read_text() == before                      # nothing written


def test_save_writes_a_referenced_step_root_proto(controller, workspace):
    doc = load(workspace, SHARED_PROTO) | {"instruction": "Trim, lower-case and dedupe spaces."}
    result = controller.design.save("p2", request(write(workspace, SHARED_PROTO, doc)))
    assert load(workspace, SHARED_PROTO)["instruction"] == "Trim, lower-case and dedupe spaces."
    assert result.files[0].revision == revision(workspace, SHARED_PROTO)


def test_a_reference_written_in_the_same_save_brings_its_proto_into_scope(controller, workspace):
    shared = load(workspace, SHARED_PROTO) | {"instruction": "Normalise."}
    with pytest.raises(OutOfScope):
        controller.design.save("p1", request(write(workspace, SHARED_PROTO, shared)))
    doc = load(workspace, P1_YAML)
    doc["steps"]["norm"] = {"use": "shared:normalise"}
    controller.design.save("p1", request(write(workspace, P1_YAML, doc), write(workspace, SHARED_PROTO, shared)))
    assert load(workspace, SHARED_PROTO)["instruction"] == "Normalise."


def test_save_writes_and_recomputes(controller, workspace, git):
    head = git(workspace, "rev-parse", "HEAD").strip()
    doc = load(workspace, P1_YAML) | {"goal": "Shout and count."}
    new = {"kind": "proto_step", "name": "extra", "instruction": "Say more.", "examples": []}
    result = controller.design.save("p1", request(write(workspace, P1_YAML, doc),
                                                  write(workspace, "processes/p1/proto/extra.yaml", new)))
    assert [f.path for f in result.files] == [P1_YAML, "processes/p1/proto/extra.yaml"]
    for saved in result.files:
        assert saved.revision == revision(workspace, saved.path)
        assert saved.yaml == (workspace / saved.path).read_text()
    assert load(workspace, P1_YAML)["goal"] == "Shout and count."
    assert list(load(workspace, P1_YAML)) == list(doc)                        # key order kept
    assert load(workspace, "processes/p1/proto/extra.yaml") == new
    assert result.validation.ok and sorted(result.steps) == ["count", "upper"]
    assert result.interface.inputs["required"] == ["text"]
    assert (result.commit, result.head) == (None, head)
    assert not list(workspace.glob("processes/p1/**/.*.tmp"))
    again = controller.design.get("p1")
    assert again.process_file.revision == result.files[0].revision and again.process_file.doc["goal"] == doc["goal"]
    assert "processes/p1/proto/extra.yaml" in again.protos


def test_validation_errors_never_block_a_save(controller, workspace):
    doc = load(workspace, P1_YAML)
    doc["edges"][0]["to"] = "counter"
    result = controller.design.save("p1", request(write(workspace, P1_YAML, doc)))
    assert load(workspace, P1_YAML)["edges"][0]["to"] == "counter"
    assert not result.validation.ok and "E-STEP-UNKNOWN" in {i.code for i in result.validation.issues}
    broken = controller.design.save("p1", request(write(workspace, P1_YAML, {"kind": "process", "bogus": 1})))
    assert not broken.validation.ok and (broken.steps, broken.interface) == ({}, ProcessInterface())


@pytest.mark.parametrize("bad", [
    {"path": P1_YAML, "doc": ["not", "a", "mapping"]},
    {"path": P1_YAML, "doc": None},
    {"path": P1_YAML, "delete": True},
])
def test_save_refuses_documents_that_are_not_objects_and_deleting_the_process(controller, workspace, bad):
    before = (workspace / P1_UPPER).read_text()
    upper = load(workspace, P1_UPPER) | {"instruction": "changed"}
    req = request(write(workspace, P1_UPPER, upper), {"base_revision": revision(workspace, P1_YAML), **bad})
    with pytest.raises(Invalid):
        controller.design.save("p1", req)
    assert (workspace / P1_UPPER).read_text() == before


def test_save_refuses_the_same_path_twice(controller, workspace):
    with pytest.raises(Invalid, match="at most once"):
        req = request(write(workspace, P1_YAML), write(workspace, "processes/p1/./process.yaml"))
        controller.design.save("p1", req)


def test_a_revision_conflict_writes_nothing(controller, workspace):
    before = {p: (workspace / p).read_text() for p in (P1_YAML, P1_UPPER)}
    doc = load(workspace, P1_YAML) | {"goal": "new goal"}
    upper = load(workspace, P1_UPPER) | {"instruction": "new instruction"}
    stale = {**write(workspace, P1_UPPER, upper), "base_revision": "sha256:" + "0" * 64}
    with pytest.raises(RevisionConflict) as err:
        controller.design.save("p1", request(write(workspace, P1_YAML, doc), stale))
    assert err.value.http == 409
    assert err.value.details == {"path": P1_UPPER, "current_revision": revision(workspace, P1_UPPER)}
    assert {p: (workspace / p).read_text() for p in before} == before

    with pytest.raises(RevisionConflict) as err:                               # null base: must not exist
        controller.design.save("p1", request({**write(workspace, P1_UPPER, upper), "base_revision": None}))
    assert err.value.details["current_revision"] == revision(workspace, P1_UPPER)
    with pytest.raises(RevisionConflict) as err:                               # a base for a missing file
        controller.design.save("p1", request({"path": "processes/p1/proto/new.yaml", "base_revision": "sha256:1",
                                              "doc": {"kind": "proto_step"}}))
    assert err.value.details == {"path": "processes/p1/proto/new.yaml", "current_revision": None}
    assert not (workspace / "processes/p1/proto/new.yaml").exists()


def test_exit_codes_keys_are_written_as_ints(controller, workspace):
    doc = {"kind": "proto_step", "name": "shell", "instruction": "Run it.",
           "exit_codes": {"0": "done", "3": "empty", "*": "error"}}             # as the web sends them (JSON keys)
    controller.design.save("p1", request({"path": "processes/p1/proto/shell.yaml", "base_revision": None, "doc": doc}))
    assert load(workspace, "processes/p1/proto/shell.yaml")["exit_codes"] == {0: "done", 3: "empty", "*": "error"}
    assert list(load(workspace, "processes/p1/proto/shell.yaml")) == list(doc)


# --- commit ---------------------------------------------------------------------------------------------------------

def test_save_with_commit(controller, workspace, git):
    doc = load(workspace, P1_YAML) | {"goal": "Committed goal."}
    result = controller.design.save("p1", request(write(workspace, P1_YAML, doc),
                                                  commit={"reason": "blur", "summary": "edit goal"}))
    head = git(workspace, "rev-parse", "HEAD").strip()
    assert result.commit.model_dump() == {"sha": head, "message": "design(p1): edit goal"}
    assert result.head == head and committed_paths(git, workspace) == [P1_YAML]
    assert git(workspace, "log", "-1", "--format=%B").strip() == (
        "design(p1): edit goal\n\nWynd-Origin: web\nWynd-Reason: blur")
    assert status_lines(git, workspace) == []
    assert controller.ctx.git_lock.entered >= 1


def test_commit_stays_inside_the_process(controller, workspace, git, write_files):
    doc = load(workspace, P1_YAML) | {"goal": "Only p1."}
    write_files(workspace, {
        P1_YAML: yaml.safe_dump(doc, sort_keys=False),
        "processes/p1/proto/extra.yaml": "kind: proto_step\nname: extra\ninstruction: More.\n",   # untracked
        "processes/p1/proto/notes.txt": "not a proto",
        "processes/p1/steps/upper/upper.py": (workspace / "processes/p1/steps/upper/upper.py").read_text() + "\n",
        P2_YAML: (workspace / P2_YAML).read_text() + "# edited\n",
        SHARED_PROTO: (workspace / SHARED_PROTO).read_text() + "# edited\n",
    })
    git(workspace, "add", "--", P2_YAML)                                        # someone else's staged change
    info = controller.design.commit("p1", reason="chat_turn", summary="  Only\n p1 ", origin="chat",
                                    extra_trailers={"Wynd-Chat": "chat_1"})
    assert committed_paths(git, workspace) == [P1_YAML, "processes/p1/proto/extra.yaml"]
    assert info.sha == git(workspace, "rev-parse", "HEAD").strip() and info.short == info.sha[:len(info.short)]
    assert info.subject == "design(p1): Only p1" and info.author == "Wynd Test"
    assert info.message == "design(p1): Only p1\n\nWynd-Origin: chat\nWynd-Reason: chat_turn\nWynd-Chat: chat_1"
    assert git(workspace, "log", "-1", "--format=%B").strip() == info.message
    assert status_lines(git, workspace) == [
        " M processes/p1/steps/upper/upper.py",
        " M shared/steps/normalise/proto.yaml",
        "?? processes/p1/proto/notes.txt",
        "M  processes/p2/process.yaml",                                         # still staged, not committed
    ]
    assert controller.design.commit("p1", reason="blur", summary="") is None   # nothing left in scope
    shared = controller.design.commit("p2", reason="blur", summary="")         # p2 references the shared proto
    assert committed_paths(git, workspace) == [P2_YAML, SHARED_PROTO]
    assert shared.subject == "design(p2): update process.yaml, proto.yaml"


def test_deletions_are_committed(controller, workspace, git):
    delete = {"path": P1_COUNT, "base_revision": revision(workspace, P1_COUNT), "delete": True}
    result = controller.design.save("p1", request(delete))
    assert not (workspace / P1_COUNT).exists()
    assert (result.files[0].revision, result.files[0].yaml) == (None, None)
    assert result.steps["count"].phase == "handwritten"                        # the package is left alone
    info = controller.design.commit("p1", reason="before_job", summary="")
    assert info.subject == "design(p1): update count.yaml"
    assert git(workspace, "show", "--name-status", "--format=", "HEAD").split() == ["D", P1_COUNT]


def test_commit_in_a_subdir_workspace(subdir_workspace, make_controller, git):
    controller = make_controller(subdir_workspace)
    doc = load(subdir_workspace, P1_YAML) | {"goal": "In a subdirectory."}
    result = controller.design.save("p1", request(write(subdir_workspace, P1_YAML, doc),
                                                  commit={"reason": "hidden", "summary": "subdir"}))
    assert result.commit.message == "design(p1): subdir"
    assert committed_paths(git, subdir_workspace) == [f"examples/ws/{P1_YAML}"]


# --- turn lock ------------------------------------------------------------------------------------------------------

def test_a_turn_lock_blocks_everyone_but_its_owner(controller, workspace):
    design = controller.design
    design.lock("p1", "chat_1", "turn_1")
    design.lock("p1", "chat_1", "turn_1")                                       # re-entrant for the owner
    assert design.locked_by("p1") == ("chat_1", "turn_1") and design.locked_by("p2") is None
    assert design.get("p1").locked_by.model_dump() == {"chat_id": "chat_1", "turn_id": "turn_1"}

    before = (workspace / P1_YAML).read_text()
    doc = load(workspace, P1_YAML) | {"goal": "web edit"}
    with pytest.raises(DesignLocked) as err:
        design.save("p1", request(write(workspace, P1_YAML, doc)))
    assert (err.value.http, err.value.details) == (423, {"chat_id": "chat_1", "turn_id": "turn_1"})
    with pytest.raises(DesignLocked):
        design.save("p1", request(write(workspace, P1_YAML, doc)), origin="chat", lock_owner=("chat_2", "turn_9"))
    assert (workspace / P1_YAML).read_text() == before
    with pytest.raises(DesignLocked):
        design.lock("p1", "chat_2", "turn_2")
    design.save("p2", request(write(workspace, P2_YAML)))                      # other processes are not locked

    design.save("p1", request(write(workspace, P1_YAML, doc | {"goal": "chat edit"})), origin="chat",
                lock_owner=("chat_1", "turn_1"))
    assert load(workspace, P1_YAML)["goal"] == "chat edit"

    design.unlock("p1", "turn_other")
    assert design.locked_by("p1") == ("chat_1", "turn_1")
    design.unlock("p1", "turn_1")
    assert design.locked_by("p1") is None and design.get("p1").locked_by is None
    design.save("p1", request(write(workspace, P1_YAML, doc)))
    assert load(workspace, P1_YAML)["goal"] == "web edit"


def test_save_checks_scope_then_lock_then_revision_then_write(controller, workspace):
    """`$DRAFTS/06 §5.7` / `$DRAFTS/07 §12.3`: each request fails every later check too, so the earliest one wins."""
    design = controller.design
    stale = "sha256:" + "0" * 64
    malformed = {"path": P1_YAML, "base_revision": stale, "doc": ["not", "a", "mapping"]}
    twice = [{**malformed, "doc": {"kind": "process"}}, {**malformed, "path": "processes/p1/./process.yaml"}]
    design.lock("p1", "chat_1", "turn_1")
    before = {p: (workspace / p).read_text() for p in (P1_YAML, P2_YAML)}

    with pytest.raises(OutOfScope):
        design.save("p1", request(malformed, {**malformed, "path": P2_YAML}))
    with pytest.raises(DesignLocked):
        design.save("p1", request(malformed))
    with pytest.raises(DesignLocked):
        design.save("p1", request(*twice))
    with pytest.raises(DesignLocked):
        design.save("p1", request({"path": P1_YAML, "base_revision": stale, "delete": True}))
    design.unlock("p1", "turn_1")
    with pytest.raises(RevisionConflict):
        design.save("p1", request(malformed))
    with pytest.raises(RevisionConflict):
        design.save("p1", request(*twice))
    current = revision(workspace, P1_YAML)
    with pytest.raises(Invalid, match="JSON object"):
        design.save("p1", request({**malformed, "base_revision": current}))
    with pytest.raises(Invalid, match="at most once"):
        design.save("p1", request(*({**w, "base_revision": current} for w in twice)))
    assert {p: (workspace / p).read_text() for p in before} == before

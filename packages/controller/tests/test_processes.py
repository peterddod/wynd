"""`ProcessService` (PLAN §8.1 processes row; `$DRAFTS/06 §5.4`) on the `ws_basic` fixture workspace."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
import yaml

from wynd.controller.errors import Conflict, Invalid, NotFound
from wynd.controller.models import ExprCheckRequest
from wynd.process.artefacts import BuildInfo
from wynd.spec.env_manifest import EnvManifest

P1_YAML = "processes/p1/process.yaml"


def put_build(controller, pid: str, commit: str, created_at: datetime) -> BuildInfo:
    staged = controller.ctx.state_dir / "tmp" / f"stage-{commit[:7]}"
    staged.mkdir(parents=True)
    info = BuildInfo(process=pid, commit=commit, source_sha=commit, job_id="job_1", process_hash="sha256:" + "0" * 64,
                     image=f"wynd/{pid}:{commit[:12]}", image_id=None, image_digest=None, base={},
                     manifest=EnvManifest(process=pid, vars=[]), created_at=created_at, dir="")
    return controller.ctx.artefacts.put_build(staged, info)


def head(git, ws) -> str:
    return git(ws, "rev-parse", "HEAD").strip()


# --- list / get / validate ----------------------------------------------------------------------------------------

def test_list_is_sorted_and_carries_status(controller):
    summaries = controller.processes.list()
    assert [(s.id, s.name, s.path) for s in summaries] == [
        ("p1", "p1", "processes/p1"), ("p2", "p2", "processes/p2"), ("parent", "parent", "processes/parent")]
    by_id = {s.id: s for s in summaries}
    assert by_id["p1"].goal == "Upper-case a text, count its words and save it."
    assert (by_id["p1"].status.design, by_id["p2"].status.design, by_id["parent"].status.design) == (False, True, False)
    assert all(s.error is None for s in summaries)


def test_list_isolates_a_process_that_does_not_load(controller, workspace, write_files):
    write_files(workspace, {"processes/p2/process.yaml": "kind: process\nname: [unclosed\n"})
    by_id = {s.id: s for s in controller.processes.list()}
    assert by_id["p2"].status is None and "E-YAML" in by_id["p2"].error
    assert by_id["p1"].error is None and by_id["p1"].status is not None


def test_get_and_unknown_ids(controller):
    assert controller.processes.get("parent").goal == "Run p1 as a child process."
    for call in (controller.processes.get, controller.processes.status, controller.processes.validate,
                 controller.processes.steps, controller.processes.history):
        with pytest.raises(NotFound, match="unknown process 'nope'"):
            call("nope")


def test_validate_reports_issues_as_dtos(controller, workspace, write_files):
    assert controller.processes.validate("p1").model_dump() == {"ok": True, "issues": []}
    text = (workspace / P1_YAML).read_text().replace("to: count\n", "to: counter\n")
    write_files(workspace, {P1_YAML: text})
    report = controller.processes.validate("p1")
    assert not report.ok
    issue = next(i for i in report.issues if i.code == "E-STEP-UNKNOWN")
    assert (issue.severity, issue.file, issue.loc) == ("error", P1_YAML, ["edges", 0, "to", 0, "step"])
    assert "counter" in issue.message
    assert "E209" in {i.code for i in report.issues}                            # count is now unreachable


def test_validate_a_process_that_does_not_load(controller, workspace, write_files):
    write_files(workspace, {P1_YAML: "kind: process\nname: p1\nentry: [\n"})
    report = controller.processes.validate("p1")
    assert not report.ok and report.issues[0].code.startswith("E-YAML")
    assert set(controller.processes.validate_all()) == {"p1", "p2", "parent"}


# --- steps and the catalog ----------------------------------------------------------------------------------------

def test_steps_of_a_compiled_process(controller):
    steps = controller.processes.steps("p1")
    upper, count = steps["upper"], steps["count"]
    assert (upper.ref_kind, upper.resolved, upper.kind, upper.phase) == ("local", True, "deterministic", "compiled")
    assert upper.proto_path == "processes/p1/proto/upper.yaml"
    assert upper.source_path == "processes/p1/steps/upper"
    assert upper.instruction == "Upper-case the text; blank text takes the empty exit."
    assert upper.used_by == ["p1"]
    assert upper.interface.source == "compiled"
    assert [e.name for e in upper.interface.exits] == ["done", "empty"]           # no implicit `error`
    assert upper.interface.inputs["required"] == ["text"]
    assert count.lock.model_dump() == {"provider": None, "tier": None, "thinking": None,
                                       "effects": ["filesystem"], "env": []}


def test_steps_of_root_design_and_process_references(controller, workspace, commit):
    commit(workspace, "p3", {"processes/p3/process.yaml": yaml.safe_dump({
        "kind": "process", "name": "p3", "entry": "norm", "inputs": {"name": "string"},
        "outputs": {"done": {"name": "string"}},
        "steps": {"norm": {"use": "shared:normalise"}, "gone": {"use": "./steps/gone"}},
        "edges": [{"from": "norm.done", "to": "$exit.done", "with": {"name": "steps.norm.outputs.name"}}],
    })})
    p2 = controller.processes.steps("p2")
    assert (p2["normalise"].ref_kind, p2["normalise"].phase) == ("root", "compiled")
    assert p2["normalise"].used_by == ["p2", "p3"]                                  # every referencing process
    tag = p2["tag"]
    assert (tag.kind, tag.phase, tag.source_path, tag.lock) == (None, "design", None, None)
    assert tag.interface.source == "declared" and [e.name for e in tag.interface.exits] == ["done"]

    child = controller.processes.steps("parent")["child"]
    assert (child.ref_kind, child.kind, child.phase, child.resolved) == ("process", "process", "compiled", True)
    assert child.interface.source == "process" and [e.name for e in child.interface.exits] == ["done", "empty"]
    assert child.instruction == "Upper-case a text, count its words and save it."
    assert child.used_by == ["parent"] and child.source_path == "processes/p1"

    gone = controller.processes.steps("p3")["gone"]
    assert (gone.resolved, gone.phase, gone.kind, gone.interface.source) == (False, "missing", None, None)


def test_step_phases_handwritten_inferred_and_child_design(controller, workspace, write_files):
    write_files(workspace, {
        "processes/p1/proto/upper.yaml": None,                                 # a lock without a proto
        "processes/p1/proto/count.yaml": "kind: proto_step\nname: count\ninstruction: Count.\n"
                                         "examples:\n  - inputs: {text: a b, dest: x}\n    outputs: {words: 2}\n",
    })
    steps = controller.processes.steps("p1")
    assert (steps["upper"].phase, steps["upper"].interface.source) == ("handwritten", "compiled")
    assert (steps["count"].phase, steps["count"].kind) == ("design", "deterministic")    # stale lock
    assert steps["count"].interface.source == "inferred"                        # from the examples only
    assert controller.processes.steps("parent")["child"].phase == "design"


def test_step_catalog_lists_every_step_root_step(controller, workspace, write_files):
    write_files(workspace, {"shared/steps/extra/proto.yaml": "kind: proto_step\nname: extra\ninstruction: Extra.\n"
                                                             "inputs: {a: string}\noutputs: {b: string}\n"})
    catalog = controller.processes.step_catalog()
    assert [e.model_dump() for e in catalog] == [
        {"use": "shared:extra", "root": "shared", "path": "extra", "kind": None, "phase": "design",
         "instruction": "Extra.", "used_by": []},
        {"use": "shared:normalise", "root": "shared", "path": "normalise", "kind": "deterministic",
         "phase": "compiled", "instruction": "Trim and lower-case the name.", "used_by": ["p2"]},
    ]


# --- interface, builds, files, history ----------------------------------------------------------------------------

def test_interface_of_the_working_tree_and_of_a_commit(controller, workspace, git, commit):
    first = head(git, workspace)
    now = controller.processes.interface("p1")
    assert now.commit is None
    assert now.inputs["properties"] == {"text": {"type": "string"}}
    assert list(now.outputs) == ["done", "empty"]
    assert [(e.inputs, e.exit) for e in now.examples] == [({"text": "hello world"}, "done"), ({"text": "   "}, "empty")]

    old_inputs = "inputs:\n  text: string\n"
    text = (workspace / P1_YAML).read_text().replace(old_inputs, old_inputs + "  n: integer?\n")
    commit(workspace, "add input", {P1_YAML: text})
    assert set(controller.processes.interface("p1").inputs["properties"]) == {"text", "n"}
    old = controller.processes.interface("p1", commit=first[:10])
    assert old.commit == first and set(old.inputs["properties"]) == {"text"}
    with pytest.raises(NotFound, match="not a commit"):
        controller.processes.interface("p1", commit="0" * 40)


def test_builds_newest_first_against_the_process_head(controller, workspace, git, commit):
    first = head(git, workspace)
    put_build(controller, "p1", first, datetime(2026, 9, 22, 10, tzinfo=UTC))
    [build] = controller.processes.builds("p1")
    assert (build.commit, build.short, build.at_head, build.behind) == (first, first[:7], True, 0)
    assert build.job_id == "job_1"
    assert build.image == f"wynd/p1:{first[:12]}"

    commit(workspace, "p2 only", {"processes/p2/notes.md": "unrelated\n"})
    second = commit(workspace, "p1 change", {"processes/p1/notes.md": "related\n"})
    put_build(controller, "p1", second, datetime(2026, 9, 22, 11, tzinfo=UTC))
    builds = controller.processes.builds("p1")
    assert [(b.commit, b.at_head, b.behind) for b in builds] == [(second, True, 0), (first, False, 1)]
    assert controller.processes.builds("p2") == []


def test_read_file_inside_the_closure_only(controller, workspace, write_files):
    content = controller.processes.read_file("p1", P1_YAML)
    data = (workspace / P1_YAML).read_bytes()
    assert content.content == data.decode() and content.size == len(data) and not content.truncated
    assert content.revision == "sha256:" + hashlib.sha256(data).hexdigest()
    assert controller.processes.read_file("p2", "shared/steps/normalise/normalise.py").path == \
        "shared/steps/normalise/normalise.py"                                  # a referenced step root
    assert controller.processes.read_file("p1", "wynd.yaml").content.startswith("process_roots")

    write_files(workspace, {"processes/p1/big.txt": "x" * (300 * 1024), "processes/p1/.env": "SECRET=1\n"})
    big = controller.processes.read_file("p1", "processes/p1/./big.txt")
    assert big.truncated and big.size == 300 * 1024 and len(big.content) == 256 * 1024
    for path in ("processes/p2/process.yaml", "../outside.txt", "/etc/hosts", "processes/p1/.env",
                 "processes/p1/missing.yaml", "processes/p1"):
        with pytest.raises(NotFound):
            controller.processes.read_file("p1", path)


def test_step_source(controller, workspace, write_files):
    write_files(workspace, {"processes/p1/steps/count/cassettes/abc.json": "{}"})
    source = controller.processes.step_source("p1", "count")
    assert source.package_dir == "processes/p1/steps/count"
    assert "entrypoint: count:Count" in source.lock_yaml
    assert [f.path for f in source.files] == ["processes/p1/steps/count/count.py",
                                              "processes/p1/steps/count/pyproject.toml",
                                              "processes/p1/steps/count/test_count.py"]
    assert source.cassettes == [{"path": "processes/p1/steps/count/cassettes/abc.json", "size": 2}]
    with pytest.raises(NotFound, match="no compiled package"):
        controller.processes.step_source("p2", "tag")
    with pytest.raises(NotFound, match="no step 'nope'"):
        controller.processes.step_source("p1", "nope")


def test_history_follows_the_reference_closure(controller, workspace, commit):
    commit(workspace, "touch p2", {"processes/p2/notes.md": "x\n"})
    commit(workspace, "touch shared", {"shared/steps/normalise/notes.md": "x\n"})
    commit(workspace, "touch p1", {"processes/p1/notes.md": "x\n"})
    assert [c.subject for c in controller.processes.history("p1")] == ["touch p1", "initial"]
    assert [c.subject for c in controller.processes.history("p2")] == ["touch shared", "touch p2", "initial"]
    assert [c.subject for c in controller.processes.history("parent", limit=1)] == ["touch p1"]   # child closure


# --- expressions --------------------------------------------------------------------------------------------------

def test_check_expr_on_the_in_flight_document(controller, workspace):
    doc = yaml.safe_load((workspace / P1_YAML).read_text())
    ok = controller.processes.check_expr(ExprCheckRequest(
        process_id="p1", process=doc, loc=["edges", 0, "with", "text"], expr="steps.upper.outputs.text", scope=True))
    assert ok.ok and ok.errors == [] and "steps.upper.outputs.text" in ok.scope
    bad = controller.processes.check_expr(ExprCheckRequest(
        process_id="p1", process=doc, loc=["edges", 0, "with", "text"], expr="steps.uper.outputs.text"))
    assert not bad.ok and bad.scope is None
    assert "unknown step 'uper'" in bad.errors[0].message
    assert (bad.errors[0].start, bad.errors[0].end) == (0, len("steps.uper.outputs.text"))
    with pytest.raises(Invalid):
        controller.processes.check_expr(ExprCheckRequest(process_id="p1", process=None, loc=[], expr="true"))


# --- tests and interface sync (real venvs, offline) ---------------------------------------------------------------

def test_test_runs_replay_and_records_on_a_clean_closure(controller, workspace, git, write_files):
    report = controller.processes.test("p1")
    assert report.passed and report.recorded and report.commit == head(git, workspace)
    assert [s.subject for s in report.suites] == ["p1#count", "p1#upper", "process:p1"]
    status = controller.processes.status("p1")
    assert (status.tests, status.compiled) == ("passed", True)

    write_files(workspace, {"processes/p1/notes.md": "dirty\n"})
    dirty = controller.processes.test("p1")
    assert dirty.passed and not dirty.recorded and dirty.commit is None


def test_test_refuses_a_design_process(controller):
    with pytest.raises(Conflict, match="design phase"):
        controller.processes.test("p2")


def test_sync_interfaces_reports_no_change_when_locks_match(controller, workspace, git):
    assert controller.processes.sync_interfaces("p1") == []
    assert git(workspace, "status", "--porcelain") == ""

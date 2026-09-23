"""Run plans: build_plan (local vs image), plan_local and the §6.4 edge venv rule (PLAN §3.11, §6.4)."""

from __future__ import annotations

import json

import pytest
from support.proc_env_workspaces import SDK, lock_only, to_yaml, validate_double  # noqa: F401

import wynd.process.validation as validation
import wynd.process.venvs as venvs
from wynd.process.errors import DesignPhase, ValidationFailed
from wynd.process.fragments import merge_process_fragments
from wynd.process.plan import assign_edge_venvs, build_plan, plan_local
from wynd.process.venvs import RuntimeSource, local_venv_id, venv_groups
from wynd.process.workspace import load_workspace
from wynd.spec.errors import Diagnostic
from wynd.spec.lockfiles import EdgeLockEntry, EdgesLock, check_hash, dump_lock
from wynd.spec.plan import PlanVenv

PINNED = RuntimeSource("0.1.0", None)
CHECK = "The note is worth keeping."

GATED = {
    "kind": "process", "name": "gated", "entry": "read", "inputs": {"src": "path"}, "outputs": {"n": "integer"},
    "steps": {"read": {"use": "./steps/read"}, "save": {"use": "./steps/save"}, "sub": {"use": "process:helper"}},
    "edges": [
        {"from": "read.done", "kind": "agentic", "to": [
            {"step": "save", "name": "keep", "check": CHECK},
            {"step": "$exit.done", "with": {"n": "0"}},
        ]},
        {"from": "save.done", "to": "sub"},
        {"from": "sub.done", "to": "$exit.done", "with": {"n": "steps.sub.outputs.n"}},
    ],
}
HELPER = {
    "kind": "process", "name": "helper", "entry": "count", "outputs": {"n": "integer"},
    "steps": {"count": {"use": "./steps/count"}},
    "edges": [{"from": "count.done", "to": "$exit.done", "with": {"n": "1"}}],
}


def gated_files(gated=GATED, edges_lock: EdgesLock | None = None, **steps) -> dict[str, str]:
    """A deterministic-only process (read with pypdf, save, a child `helper`) with one agentic branch."""
    lock = edges_lock or EdgesLock(edges={"read.done[keep]": EdgeLockEntry(check_hash=check_hash(CHECK, None),
                                                                         tier="standard")})
    return {
        "wynd.yaml": "process_roots: [processes]\n",
        "processes/gated/process.yaml": to_yaml(gated),
        "processes/gated/edges.lock.yaml": dump_lock(lock),
        "processes/helper/process.yaml": to_yaml(HELPER),
        **lock_only("processes/gated/steps/read", fragment={"deps": ["pypdf>=6,<7"]}),
        **lock_only("processes/gated/steps/save"),
        **lock_only("processes/helper/steps/count"),
        **{k: v for files in steps.values() for k, v in files.items()},
    }


@pytest.fixture
def created(monkeypatch):
    """Stands in for venv creation: records each group and returns its local id."""
    groups = []

    def ensure(group, *, venv_root, runtime, log):
        groups.append(group)
        return local_venv_id(group, runtime)

    monkeypatch.setattr(venvs, "ensure_local_venv", ensure)
    return groups


def dump(plan, ws) -> dict:
    data = plan.model_dump(mode="json", exclude={"steps": {"__all__": {"lock"}},
                                                 "processes": {"__all__": {"definition"}}})
    return json.loads(json.dumps(data).replace(str(ws), "<ws>"))


EMPTY, PYPDF, SDK_VENV = "b1cc4cae201b7428", "e30b58de1ee429a8", "f26e9e5382965490"
GOLDEN_LOCAL = {
    "wynd": 1, "mode": "local", "root": "gated", "commit": None, "provider": "claude-code",
    "venv_root": "<ws>/.wynd/venvs",
    "venvs": [
        {"id": EMPTY, "python": None, "steps": ["gated#save", "helper#count"]},
        {"id": PYPDF, "python": None, "steps": ["gated#read"]},
        {"id": SDK_VENV, "python": None, "steps": []},                          # added for the agentic branch
    ],
    "steps": {
        "gated#read": {"id": "gated#read", "kind": "deterministic", "entrypoint": "read:Read", "venv": PYPDF,
                       "package_dir": "<ws>/processes/gated/steps/read"},
        "gated#save": {"id": "gated#save", "kind": "deterministic", "entrypoint": "save:Save", "venv": EMPTY,
                       "package_dir": "<ws>/processes/gated/steps/save"},
        "helper#count": {"id": "helper#count", "kind": "deterministic", "entrypoint": "count:Count", "venv": EMPTY,
                         "package_dir": "<ws>/processes/helper/steps/count"},
    },
    "processes": {
        "gated": {
            "id": "gated", "dir": "<ws>/processes/gated",
            "nodes": {"read": {"step": "gated#read", "process": None}, "save": {"step": "gated#save", "process": None},
                      "sub": {"step": None, "process": "helper"}},
            "edges_lock": {"wynd": 1, "edges": {"read.done[keep]": {
                "check_hash": "sha256:da609cac19451e3c6029c2a76b64296f2988567bac2beac760efb44b78bb05f0",
                "provider": None, "tier": "standard", "thinking": "low", "retries": 2, "timeout_s": 120}}},
        },
        "helper": {
            "id": "helper", "dir": "<ws>/processes/helper",
            "nodes": {"count": {"step": "helper#count", "process": None}},
            "edges_lock": {"wynd": 1, "edges": {}},
        },
    },
    "edge_venvs": {"gated:read.done[keep]": SDK_VENV},
}


def test_plan_local_golden_with_an_added_edge_venv(make_repo, validate_double, created):
    ws = make_repo(files=gated_files())
    workspace = load_workspace(ws)
    plan = plan_local(workspace, "gated", runtime=PINNED)
    assert dump(plan, ws) == GOLDEN_LOCAL
    assert [(g.requirements, g.steps) for g in created] == [((), ("gated#save", "helper#count")),
                                                             (("pypdf>=6,<7",), ("gated#read",)),
                                                             ((SDK,), ())]
    lp = workspace.load_process("gated")
    assert {sid: step.lock for sid, step in plan.steps.items()} == {
        sid: pkg.lock for sid, pkg in lp.closure_packages().items()}
    assert {pid: pp.definition for pid, pp in plan.processes.items()} == {
        pid: proc.doc for pid, proc in lp.closure_processes().items()}
    assert plan.venv_root == str(ws / ".wynd" / "venvs")


def test_plan_local_honours_venv_root(make_repo, validate_double, created, tmp_path):
    ws = make_repo(files=gated_files())
    plan = plan_local(load_workspace(ws), "gated", venv_root=tmp_path / "elsewhere", runtime=PINNED)
    assert plan.venv_root == str(tmp_path / "elsewhere")


def test_local_and_image_plans_differ_only_in_mode_venv_root_and_paths(make_repo, validate_double):
    ws = make_repo(files=gated_files())
    lp = load_workspace(ws).load_process("gated")
    report = validation.validate(lp)
    keyed = [PlanVenv(id=g.key, steps=list(g.steps)) for g in venv_groups(merge_process_fragments(lp))]
    local = build_plan(lp, report, mode="local", venv_root=str(ws / ".wynd/venvs"), venvs=keyed, ws_root=ws)
    image = build_plan(lp, report, mode="image", venv_root="/opt/wynd/venvs", venvs=keyed, ws_root=ws)
    assert image.mode == "image" and image.venv_root == "/opt/wynd/venvs"
    assert all(step.package_dir is None for step in image.steps.values())
    assert all(process.dir is None for process in image.processes.values())
    assert image.processes["gated"].edges_lock == local.processes["gated"].edges_lock   # read from ws_root
    normalised = local.model_copy(update={
        "mode": "image", "venv_root": "/opt/wynd/venvs",
        "steps": {sid: step.model_copy(update={"package_dir": None}) for sid, step in local.steps.items()},
        "processes": {pid: pp.model_copy(update={"dir": None}) for pid, pp in local.processes.items()},
    })
    assert normalised.model_dump(mode="json") == image.model_dump(mode="json")
    assert assign_edge_venvs(image) == assign_edge_venvs(local)

    without_root = build_plan(lp, report, mode="image", venv_root="/opt/wynd/venvs", venvs=keyed, ws_root=None)
    assert without_root.processes["gated"].edges_lock == EdgesLock()
    with pytest.raises(ValueError, match="ws_root"):
        build_plan(lp, report, mode="local", venv_root="/x", venvs=keyed, ws_root=None)
    without_read = [venv for venv in keyed if "gated#read" not in venv.steps]
    with pytest.raises(ValueError, match="gated#read is not served"):
        build_plan(lp, report, mode="image", venv_root="/x", venvs=without_read, ws_root=None)


def test_edge_venv_reuses_a_venv_that_already_has_the_provider(make_repo, validate_double):
    """An agentic step in the closure already carries claude-code's deps: the branch runs in its venv."""
    gated = {**GATED, "steps": {**GATED["steps"], "tag": {"use": "./steps/tag"}}}
    files = gated_files(gated, tag=lock_only("processes/gated/steps/tag", kind="agentic", tier="cheap"))
    ws = make_repo(files=files)
    lp = load_workspace(ws).load_process("gated")
    groups = venv_groups(merge_process_fragments(lp))
    keyed = [PlanVenv(id=g.key, steps=list(g.steps)) for g in groups]
    plan = build_plan(lp, validation.validate(lp), mode="image", venv_root="/opt", venvs=keyed, ws_root=ws)
    tag_venv = plan.steps["gated#tag"].venv
    assert assign_edge_venvs(plan) == ({"gated:read.done[keep]": tag_venv}, [])


def test_edge_lock_provider_override_and_idempotence(make_repo, validate_double):
    override = EdgesLock(edges={"read.done[keep]": EdgeLockEntry(check_hash=check_hash(CHECK, None),
                                                                 provider="fake")})
    ws = make_repo(files=gated_files(edges_lock=override))
    lp = load_workspace(ws).load_process("gated")
    groups = venv_groups(merge_process_fragments(lp))
    keyed = [PlanVenv(id=g.key, steps=list(g.steps)) for g in groups]
    plan = build_plan(lp, validation.validate(lp), mode="image", venv_root="/opt", venvs=keyed, ws_root=ws)
    # the fake provider needs nothing: the first venv in plan order serves the branch
    assert assign_edge_venvs(plan) == ({"gated:read.done[keep]": keyed[0].id}, [])

    default = build_plan(lp, validation.validate(lp), mode="image", venv_root="/opt", venvs=keyed, ws_root=None)
    edge_venvs, added = assign_edge_venvs(default)                              # no lock: plan.provider (claude-code)
    assert added == [PlanVenv(id=venvs.group_key((SDK,)), steps=[])]
    assert edge_venvs == {"gated:read.done[keep]": added[0].id}
    again = default.model_copy(update={"venvs": [*default.venvs, *added]})
    assert assign_edge_venvs(again) == (edge_venvs, [])


def test_plan_local_refuses_an_invalid_process(make_repo, monkeypatch, created):
    ws = make_repo(files=gated_files())

    def failing(lp, *, providers=None, stats=None):
        return validation.ValidationReport(process=lp.id, diagnostics=[Diagnostic("error", "E209", "unreachable")],
                                           normalized={}, env_refs={})

    monkeypatch.setattr(validation, "validate", failing)
    with pytest.raises(ValidationFailed) as err:
        plan_local(load_workspace(ws), "gated", runtime=PINNED)
    assert err.value.report.diagnostics[0].code == "E209"
    assert created == []


def test_plan_local_refuses_a_design_phase_process(make_repo, validate_double, created):
    gated = {**GATED, "steps": {**GATED["steps"], "extra": {"use": "./steps/extra"}}}
    proto = to_yaml({"kind": "proto_step", "name": "extra", "instruction": "More.",
                     "examples": [{"inputs": {"a": 1}, "outputs": {"b": 2}}]})
    ws = make_repo(files=gated_files(gated, extra={"processes/gated/proto/extra.yaml": proto}))
    with pytest.raises(DesignPhase):
        plan_local(load_workspace(ws), "gated", runtime=PINNED)
    assert created == []

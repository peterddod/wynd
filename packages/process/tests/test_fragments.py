"""Fragment merge over a process closure (PLAN §6.1 fragments row, §6.4; `$DRAFTS/04 §6.1`, §18.4)."""

from __future__ import annotations

import random

import pytest
from support.proc_env_workspaces import SDK, fake_providers, lock_only, to_yaml

from wynd.process.errors import DesignPhase
from wynd.process.fragments import StepEnv, merge_process_fragments, requirement_set
from wynd.process.venvs import venv_groups
from wynd.process.workspace import load_workspace
from wynd.spec.lockfiles import FragmentRecord

AGENTIC = {"kind": "agentic", "tier": "cheap", "thinking": "low"}


def closure_files(steps_order: list[str] | None = None, child_provider: str = "anthropic") -> dict[str, str]:
    """Root `intake` (claude-code by default) with read (pypdf, locked), draft (agentic), save (none) and a child
    `helper` that declares its own provider and has an agentic step plus a glibc/system step."""
    steps = {"read": {"use": "./steps/read"}, "draft": {"use": "./steps/draft"}, "save": {"use": "./steps/save"},
             "sub": {"use": "process:helper"}}
    order = steps_order or list(steps)
    root = {
        "kind": "process", "name": "intake", "entry": "read", "inputs": {"path": "path"},
        "outputs": {"n": "integer"},
        "steps": {key: steps[key] for key in order},
        "edges": [
            {"from": "read.done", "to": "draft"},
            {"from": "draft.done", "to": "save"},
            {"from": "save.done", "to": "sub"},
            {"from": "sub.done", "to": "$exit.done", "with": {"n": "steps.sub.outputs.n"}},
        ],
    }
    helper = {
        "kind": "process", "name": "helper", "provider": child_provider, "entry": "tag", "outputs": {"n": "integer"},
        "steps": {"tag": {"use": "./steps/tag"}, "ocr": {"use": "./steps/ocr"}},
        "edges": [{"from": "tag.done", "to": "ocr"},
                  {"from": "ocr.done", "to": "$exit.done", "with": {"n": "1"}}],
    }
    return {
        "wynd.yaml": "process_roots: [processes]\n",
        "processes/intake/process.yaml": to_yaml(root),
        "processes/helper/process.yaml": to_yaml(helper),
        **lock_only("processes/intake/steps/read", fragment={"deps": ["pypdf>=6,<7"]},
                    locked_deps=["pypdf==6.19.0"]),
        **lock_only("processes/intake/steps/draft", **AGENTIC),
        **lock_only("processes/intake/steps/save"),
        **lock_only("processes/helper/steps/tag", **AGENTIC),
        **lock_only("processes/helper/steps/ocr", fragment={"deps": ["PyTesseract >= 0.3"],
                                                            "system": ["tesseract-ocr", "libjpeg-dev"],
                                                            "requires": "glibc"}),
    }


def merged(ws, pid="intake", providers=fake_providers):
    return merge_process_fragments(load_workspace(ws).load_process(pid), providers)


def test_steps_requirements_and_provider_deps_only_on_agentic_steps(make_repo):
    env = merged(make_repo(files=closure_files()))
    assert env.steps == {
        "helper#ocr": StepEnv("helper#ocr", ("pytesseract>= 0.3",), None),
        "helper#tag": StepEnv("helper#tag", (SDK,), "claude-code"),
        "intake#draft": StepEnv("intake#draft", (SDK,), "claude-code"),
        "intake#read": StepEnv("intake#read", ("pypdf==6.19.0",), None),         # locked_deps win over the ranges
        "intake#save": StepEnv("intake#save", (), None),
    }
    assert env.providers == ("claude-code",)


def test_child_steps_use_the_root_provider_not_their_own(make_repo):
    env = merged(make_repo(files=closure_files(child_provider="anthropic")))
    assert env.steps["helper#tag"].provider == "claude-code"
    assert "provider:anthropic" not in [record.source for record in env.fragments]


def test_a_steps_own_provider_override_applies(make_repo):
    files = closure_files()
    files.update(lock_only("processes/intake/steps/draft", **AGENTIC, provider="anthropic"))
    env = merged(make_repo(files=files))
    assert env.steps["intake#draft"] == StepEnv("intake#draft", (), "anthropic")   # a ModelProvider: no deps
    assert env.providers == ("anthropic", "claude-code")


def test_system_packages_glibc_provenance_and_fragment_records(make_repo):
    env = merged(make_repo(files=closure_files()))
    assert env.system == ("libjpeg-dev", "tesseract-ocr")
    assert env.glibc_required_by == ("step:helper#ocr", "provider:claude-code")
    assert env.fragments == (
        FragmentRecord(source="step:helper#ocr", deps=["PyTesseract >= 0.3"], system=["tesseract-ocr", "libjpeg-dev"],
                       requires="glibc"),
        FragmentRecord(source="step:helper#tag", deps=[], system=[], requires=None),
        FragmentRecord(source="step:intake#draft", deps=[], system=[], requires=None),
        FragmentRecord(source="step:intake#read", deps=["pypdf>=6,<7"], system=[], requires=None),
        FragmentRecord(source="step:intake#save", deps=[], system=[], requires=None),
        FragmentRecord(source="provider:claude-code", deps=[SDK], system=[], requires="glibc"),
    )


def test_groups_are_stable_across_step_orders(make_repo):
    keys = set()
    for seed in range(3):
        order = ["read", "draft", "save", "sub"]
        random.Random(seed).shuffle(order)
        groups = venv_groups(merged(make_repo(files=closure_files(order))))
        keys.add(tuple((g.key, g.requirements, g.steps) for g in groups))
    assert len(keys) == 1
    [groups] = keys
    by_steps = {steps: reqs for _key, reqs, steps in groups}
    assert by_steps == {
        ("helper#tag", "intake#draft"): (SDK,),
        ("helper#ocr",): ("pytesseract>= 0.3",),
        ("intake#read",): ("pypdf==6.19.0",),
        ("intake#save",): (),
    }
    assert [key for key, _reqs, _steps in groups] == sorted(key for key, _reqs, _steps in groups)


def test_design_phase_step_raises(make_repo):
    files = closure_files()
    files["processes/intake/proto/extra.yaml"] = to_yaml({
        "kind": "proto_step", "name": "extra", "instruction": "Do more.",
        "examples": [{"inputs": {"a": 1}, "outputs": {"b": 2}}],
    })
    root = files["processes/intake/process.yaml"].replace("  sub:\n", "  extra:\n    use: ./steps/extra\n  sub:\n")
    files["processes/intake/process.yaml"] = root
    with pytest.raises(DesignPhase) as err:
        merged(make_repo(files=files))
    assert err.value.step_ids == ["intake#extra"]


def test_unknown_provider_contributes_no_deps(make_repo):
    files = closure_files()
    files.update(lock_only("processes/intake/steps/draft", **AGENTIC, provider="nope"))
    env = merged(make_repo(files=files))
    assert env.steps["intake#draft"] == StepEnv("intake#draft", (), "nope")
    assert "provider:nope" not in [record.source for record in env.fragments]


def test_default_lookup_is_the_runtime_registry(make_repo):
    env = merged(make_repo(files=closure_files()), providers=None)
    assert env.steps["intake#draft"].requirements == requirement_set([SDK])
    assert "provider:claude-code" in env.glibc_required_by


def test_requirement_set_normalises_and_dedupes():
    assert requirement_set(["PyPDF2>=3", "pypdf2>=3", "Foo_Bar.baz ==1", "a"]) == ("a", "foo-bar-baz==1", "pypdf2>=3")

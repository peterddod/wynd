"""Step wheels built with the real (offline) `uv build` (PLAN §3.1, §3.6, §6.5)."""

from __future__ import annotations

import json
import zipfile

import pytest
from support.proc_env_workspaces import lock_only

from wynd.process.build.resolve import UvResolver
from wynd.process.build.wheels import build_step_wheel, stage_step, wheel_dist
from wynd.process.errors import WyndProcessError
from wynd.process.hashing import step_hash
from wynd.process.workspace import CommitTree, load_workspace
from wynd.spec.workspace import step_module_name

PKG = "processes/notes/steps/tag"
AGENTIC = {"kind": "agentic", "tier": "strong", "thinking": "high", "provider": "anthropic",
           "effects": ["network"], "fragment": {"deps": ["httpx>=0.27"]}, "locked_deps": ["httpx==0.28.1"]}


def files(**lock) -> dict[str, str]:
    return {
        "wynd.yaml": "process_roots: [processes]\n",
        "processes/notes/process.yaml": "kind: process\nname: notes\nentry: tag\nsteps:\n  tag: {use: ./steps/tag}\n",
        **lock_only(PKG, **{**AGENTIC, **lock}),
        f"{PKG}/tag.py": "from .helpers import shout\n",
        f"{PKG}/helpers.py": "def shout(text):\n    return text.upper()\n",
        f"{PKG}/sub/extra.py": "VALUE = 1\n",
        f"{PKG}/test_tag.py": "def test_tag():\n    pass\n",
        f"{PKG}/cassettes/0123.json": "{}\n",
        f"{PKG}/README.md": "Not code.\n",
    }


def package(ws):
    workspace = load_workspace(ws)
    pkg = workspace.load_process("notes").steps["tag"].package
    return workspace, pkg


def test_the_wheel_holds_the_code_and_a_tierless_step_json(make_repo, tmp_path):
    ws = make_repo(files=files())
    workspace, pkg = package(ws)
    wheel = build_step_wheel(workspace.tree, pkg, pkg.lock, out=tmp_path / "dist", resolver=UvResolver(),
                             stage_root=tmp_path / "stage")
    module = step_module_name("notes#tag")
    assert wheel.name == f"{wheel_dist('notes#tag').replace('-', '_')}-0.1.0-py3-none-any.whl"
    assert wheel.name == f"wynd_step_{module}-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as zf:
        names = sorted(n for n in zf.namelist() if ".dist-info/" not in n)
        meta = json.loads(zf.read(f"wynd_steps/{module}/wynd-step.json"))
        metadata = zf.read(next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))).decode()
    assert names == [f"wynd_steps/{module}/{rel}" for rel in ("helpers.py", "sub/extra.py", "tag.py",
                                                              "wynd-step.json")]
    assert meta == {
        "schema": 1, "step": "notes#tag", "entrypoint": "tag:Tag", "kind": "agentic", "proto_hash": None,
        "step_hash": step_hash(workspace.tree, pkg), "requirements": ["httpx==0.28.1"], "effects": ["network"],
        "tools": [], "mcp": [],
    }
    assert "Requires-Dist" not in metadata                   # installed with --no-deps from the venv's pins


def test_staging_from_a_commit_matches_the_working_tree(make_repo, tmp_path, git):
    ws = make_repo(files=files())
    workspace, pkg = package(ws)
    from_tree = stage_step(workspace.tree, pkg, pkg.lock, stage_root=tmp_path / "a")
    (ws / PKG / "helpers.py").write_text("changed = True\n")          # not committed: the commit stages the old one
    commit_ws = load_workspace(ws, CommitTree(ws, "HEAD"))
    commit_pkg = commit_ws.load_process("notes").steps["tag"].package
    from_commit = stage_step(commit_ws.tree, commit_pkg, commit_pkg.lock, stage_root=tmp_path / "b")
    listing = sorted(p.relative_to(from_tree).as_posix() for p in from_tree.rglob("*") if p.is_file())
    assert listing == sorted(p.relative_to(from_commit).as_posix() for p in from_commit.rglob("*") if p.is_file())
    for rel in listing:
        assert (from_tree / rel).read_bytes() == (from_commit / rel).read_bytes()
    assert "packages = [\"wynd_steps\"]" in (from_tree / "pyproject.toml").read_text()


def test_a_bad_entrypoint_is_an_error(make_repo, tmp_path):
    ws = make_repo(files=files(entrypoint="missing:Tag"))
    workspace, pkg = package(ws)
    with pytest.raises(WyndProcessError, match="entrypoint 'missing:Tag' names no module of the package"):
        stage_step(workspace.tree, pkg, pkg.lock, stage_root=tmp_path / "stage")


def test_restaging_replaces_the_previous_stage(make_repo, tmp_path):
    ws = make_repo(files=files())
    workspace, pkg = package(ws)
    project = stage_step(workspace.tree, pkg, pkg.lock, stage_root=tmp_path / "stage")
    (project / "wynd_steps" / "stale.py").write_text("x = 1\n")
    again = stage_step(workspace.tree, pkg, pkg.lock, stage_root=tmp_path / "stage")
    assert again == project and not (project / "wynd_steps" / "stale.py").exists()


# --- the resolver (real uv, offline against the warm cache) ---------------------------------------------------------

def test_uv_resolver_pins_universally(monkeypatch):
    monkeypatch.setenv("UV_OFFLINE", "1")
    pins = UvResolver().compile(["pypdf>=6,<7"], universal=True)
    assert len(pins) == 1 and pins[0].startswith("pypdf==6.")


def test_uv_resolver_failure_is_a_resolution_error(monkeypatch):
    from wynd.process.errors import ResolutionError

    monkeypatch.setenv("UV_OFFLINE", "1")
    with pytest.raises(ResolutionError, match="No solution found"):
        UvResolver().compile(["wynd-no-such-package-xyz"], universal=True)

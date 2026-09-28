"""Venv groups, local venvs and interface snapshot sync (PLAN §6.4, §14 PROC-ENV; `$DRAFTS/04 §6.2–§6.4`,
§18.4)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from support.proc_env_workspaces import NOTES, NOTES_PROCESS, finish_locks, notes_files, pyproject, src

import wynd.process.venvs as venvs
from wynd.process.fragments import MergedEnv, StepEnv
from wynd.process.venvs import (
    RuntimeSource,
    VenvGroup,
    detect_runtime_source,
    ensure_local_venv,
    group_key,
    local_venv_id,
    step_python,
    sync_interfaces,
    venv_groups,
)
from wynd.process.workspace import load_workspace
from wynd.spec.hashing import dependency_set_hash
from wynd.spec.lockfiles import load_step_lock
from wynd.spec.yamlio import dump_yaml

PINNED = RuntimeSource("0.1.0", None)


def test_venv_groups_share_one_venv_per_requirement_set():
    env = MergedEnv(
        steps={
            "p#a": StepEnv("p#a", ("pypdf>=6,<7",), None),
            "p#b": StepEnv("p#b", (), None),
            "p#c": StepEnv("p#c", ("pypdf>=6,<7",), None),
            "q#d": StepEnv("q#d", (), None),
        },
        system=(), glibc_required_by=(), providers=(), fragments=(),
    )
    groups = venv_groups(env)
    assert {(g.requirements, g.steps) for g in groups} == {(("pypdf>=6,<7",), ("p#a", "p#c")), ((), ("p#b", "q#d"))}
    assert [g.key for g in groups] == sorted(g.key for g in groups)
    for group in groups:
        assert group.key == group_key(group.requirements) == dependency_set_hash(group.requirements)[7:23]


def test_local_venv_id_depends_on_requirements_and_runtime_only():
    a = VenvGroup("x", ("pypdf>=6,<7",), ("p#a",))
    assert local_venv_id(a, PINNED) == local_venv_id(VenvGroup("y", ("pypdf>=6,<7",), ()), PINNED)
    assert local_venv_id(a, PINNED) != local_venv_id(VenvGroup("x", (), ("p#a",)), PINNED)
    assert local_venv_id(a, PINNED) != local_venv_id(a, RuntimeSource("0.2.0", None))
    assert len(local_venv_id(a, PINNED)) == 16


@pytest.fixture
def fake_uv(monkeypatch):
    """Replaces `_proc.run`: records argv, makes the directory for `uv venv`, answers `uv pip freeze`. `hooks[verb]`
    runs before answering (e.g. to simulate a concurrent creator or a failing install)."""
    calls: list[list[str]] = []
    hooks: dict = {}

    def run(args, *, cwd, env=None, log=None, check=True, input=None):
        argv = [str(a) for a in args]
        calls.append(argv)
        verb = argv[2] if argv[1] == "pip" else argv[1]
        if verb in hooks:
            hooks[verb](argv)
        if verb == "venv":
            Path(argv[-1]).mkdir(parents=True)
        return "pytest==9.1.1\nwynd-runtime==0.1.0\n" if verb == "freeze" else ""

    monkeypatch.setattr(venvs._proc, "run", run)
    monkeypatch.setattr(venvs._proc, "require_tool", lambda name: f"/opt/bin/{name}")
    return calls, hooks


def test_ensure_local_venv_runs_uv_and_writes_the_marker_last(fake_uv, tmp_path):
    calls, _ = fake_uv
    group = VenvGroup("k", ("pypdf>=6,<7",), ("p#read",))
    root = tmp_path / "venvs"
    venv_id = ensure_local_venv(group, venv_root=root, runtime=PINNED, log=None)
    assert venv_id == local_venv_id(group, PINNED)
    tmp = calls[0][-1]
    assert Path(tmp).parent == root and Path(tmp).name.startswith(f".tmp-{venv_id}-{os.getpid()}-")
    python = f"{tmp}/bin/python"
    assert calls == [
        ["/opt/bin/uv", "venv", "--relocatable", "--python", "3.12", "--no-project", tmp],
        ["/opt/bin/uv", "pip", "install", "--python", python, "wynd-spec==0.1.0", "wynd-runtime==0.1.0",
         "pypdf>=6,<7", "pytest>=8,<10"],
        ["/opt/bin/uv", "pip", "freeze", "--python", python],
    ]
    marker = json.loads((root / venv_id / "wynd-venv.json").read_text())
    assert marker["id"] == venv_id
    assert marker["inputs"] == {"schema": 1, "python": "3.12", "requirements": ["pypdf>=6,<7"],
                                "runtime": PINNED.descriptor(), "tools": ["pytest>=8,<10"]}
    assert marker["freeze"] == ["pytest==9.1.1", "wynd-runtime==0.1.0"]
    assert not Path(tmp).exists()

    calls.clear()
    assert ensure_local_venv(group, venv_root=root, runtime=PINNED, log=None) == venv_id
    assert calls == []                                          # the marker says the venv is complete


def test_editable_runtime_is_installed_editable(fake_uv, tmp_path):
    calls, _ = fake_uv
    spec, runtime_dir = tmp_path / "packages/spec", tmp_path / "packages/runtime"
    for pkg in (spec, runtime_dir):
        pkg.mkdir(parents=True)
        (pkg / "pyproject.toml").write_text("[project]\n")
    runtime = RuntimeSource("0.1.0", (spec, runtime_dir))
    ensure_local_venv(VenvGroup("k", (), ()), venv_root=tmp_path / "venvs", runtime=runtime, log=None)
    assert calls[1][5:] == ["-e", str(spec), "-e", str(runtime_dir), "pytest>=8,<10"]


def test_a_concurrent_creator_wins_and_the_temp_dir_is_removed(fake_uv, tmp_path):
    _, hooks = fake_uv
    group = VenvGroup("k", (), ())
    venv_id = local_venv_id(group, PINNED)
    winner = tmp_path / venv_id

    def other_process_finishes_first(argv):
        winner.mkdir()
        (winner / "wynd-venv.json").write_text('{"id": "winner"}')

    hooks["install"] = other_process_finishes_first
    assert ensure_local_venv(group, venv_root=tmp_path, runtime=PINNED, log=None) == venv_id
    assert json.loads((winner / "wynd-venv.json").read_text()) == {"id": "winner"}
    assert [p.name for p in tmp_path.iterdir()] == [venv_id]  # no .tmp-* left behind


def test_a_failed_install_leaves_nothing(fake_uv, tmp_path):
    _, hooks = fake_uv

    def fail(argv):
        raise subprocess.CalledProcessError(1, argv, stderr="No solution found")

    hooks["install"] = fail
    with pytest.raises(subprocess.CalledProcessError):
        ensure_local_venv(VenvGroup("k", ("nope>=99",), ()), venv_root=tmp_path, runtime=PINNED, log=None)
    assert list(tmp_path.iterdir()) == []


def test_step_python_uses_the_same_venv_as_the_plan_group(fake_uv, tmp_path):
    python = step_python(["PyPDF>=6", "pypdf>=6"], venv_root=tmp_path, runtime=PINNED)
    venv_id = local_venv_id(VenvGroup("any", ("pypdf>=6",), ("p#read",)), PINNED)
    assert python == tmp_path / venv_id / "bin" / "python"


def test_real_offline_venv_with_editable_spec_and_runtime(shared_venvs, monkeypatch):
    monkeypatch.setenv("UV_OFFLINE", "1")
    lines: list[str] = []
    runtime = detect_runtime_source(os.environ)
    venv_id = ensure_local_venv(VenvGroup("k", (), ()), venv_root=shared_venvs, runtime=runtime, log=lines.append)
    python = shared_venvs / venv_id / "bin" / "python"
    out = subprocess.run([str(python), "-c", "import wynd.runtime, wynd.spec, pytest; print(wynd.runtime.__file__)"],
                         capture_output=True, text=True, check=True).stdout
    assert Path(out.strip()).is_relative_to(runtime.editable[1])   # the monorepo source, live
    marker = json.loads((shared_venvs / venv_id / "wynd-venv.json").read_text())
    assert any(line.startswith("pytest==") for line in marker["freeze"])
    assert step_python([], venv_root=shared_venvs, runtime=runtime) == python


# --- sync_interfaces ------------------------------------------------------------------------------------------------

COUNT_PY = src('''
    from typing import Literal

    from pydantic import BaseModel

    from wynd.runtime import ShellStep


    class Count(ShellStep):
        """Count the words of a file."""

        class Input(BaseModel):
            path: str

        class Done(BaseModel):
            exit: Literal["done"] = "done"
            stdout: str

        class Empty(BaseModel):
            exit: Literal["empty"] = "empty"

        Output = Done | Empty
        exit_codes = {0: "done", 1: "empty", "*": "error"}

        def command(self, input):
            return ["wc", "-w", input.path]
''')

TAGGER_PY = src('''
    from typing import Literal

    from pydantic import BaseModel

    from wynd.runtime import AgenticStep


    class Tagger(AgenticStep):
        """Tag the note with one word."""

        context = ["previous.outputs"]

        class Input(BaseModel):
            text: str

        class Done(BaseModel):
            exit: Literal["done"] = "done"
            tag: str

        Output = Done

        def run(self, input: Input) -> Done: ...
''')


def test_sync_interfaces_refreshes_only_drifted_snapshots(make_repo, commit):
    process = {**NOTES_PROCESS, "steps": {**NOTES_PROCESS["steps"], "count": {"use": "./steps/count"},
                                          "tagger": {"use": "./steps/tagger"}}}
    ws = make_repo(files={
        **notes_files(process),
        f"{NOTES}/steps/count/pyproject.toml": pyproject("notes-count"),
        f"{NOTES}/steps/count/count.py": COUNT_PY,
        f"{NOTES}/steps/tagger/pyproject.toml": pyproject("notes-tagger"),
        f"{NOTES}/steps/tagger/tagger.py": TAGGER_PY,
    })
    steps = {name: ws / NOTES / "steps" / name for name in ("read", "save", "count", "tagger")}
    finish_locks(ws, {f"{NOTES}/steps/{name}": {} for name in ("read", "save", "count")})
    finish_locks(ws, {f"{NOTES}/steps/tagger": {"tier": "cheap", "thinking": "low"}})
    good = {name: load_step_lock(path / "step.lock.yaml") for name, path in steps.items()}

    # read: no snapshot yet (a hand-written step), with a comment that must survive
    read_lock = good["read"].model_copy(update={"interface": None})
    read_text = "# hand-written; interface filled by sync\n" + dump_yaml(read_lock.model_dump(
        mode="json", by_alias=True, exclude_none=True, exclude_defaults=True))
    (steps["read"] / "step.lock.yaml").write_text(read_text)
    # count: stale exit codes; tagger: stale context
    count_text = (steps["count"] / "step.lock.yaml").read_text()
    stale_codes = count_text.replace("  exit_codes:\n    0: done\n    1: empty\n", "  exit_codes:\n    0: done\n")
    assert stale_codes != count_text
    (steps["count"] / "step.lock.yaml").write_text(stale_codes)
    tagger_text = (steps["tagger"] / "step.lock.yaml").read_text()
    (steps["tagger"] / "step.lock.yaml").write_text(tagger_text.replace("context:\n- previous.outputs\n", ""))
    save_text = (steps["save"] / "step.lock.yaml").read_text()

    venv_root = ws / ".wynd" / "venvs"
    changed = sync_interfaces(load_workspace(ws), "notes", venv_root=venv_root)
    assert changed == [f"{NOTES}/steps/{name}/step.lock.yaml" for name in ("count", "read", "tagger")]
    for name, path in steps.items():
        assert load_step_lock(path / "step.lock.yaml") == good[name], name
    assert (steps["read"] / "step.lock.yaml").read_text().startswith("# hand-written; interface filled by sync\n")
    assert (steps["save"] / "step.lock.yaml").read_text() == save_text           # untouched byte for byte

    assert sync_interfaces(load_workspace(ws), "notes", venv_root=venv_root) == []


def test_sync_interfaces_replaces_the_interface_block_in_place(make_repo):
    ws = make_repo(files=notes_files())
    finish_locks(ws, {f"{NOTES}/steps/read": {}, f"{NOTES}/steps/save": {}})
    path = ws / NOTES / "steps" / "save" / "step.lock.yaml"
    good = path.read_text()
    lines = good.splitlines(keepends=True)
    start = lines.index("interface:\n")                        # dump_lock writes it last: move it up front
    head, block = lines[:start], "".join(lines[start:])
    assert head[0] == "wynd: 1\n"
    path.write_text(head[0] + "interface:\n  input: {}\n  outputs: {}\n\n# the rest\n" + "".join(head[1:]))
    assert sync_interfaces(load_workspace(ws), "notes", venv_root=ws / ".wynd" / "venvs") == \
        [f"{NOTES}/steps/save/step.lock.yaml"]
    assert path.read_text() == head[0] + block + "\n# the rest\n" + "".join(head[1:])   # in place, rest kept

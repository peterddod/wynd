"""Workspace discovery, root rules, git-backed trees and the subprocess helpers (PLAN §3.1, §3.19, §6.1)."""

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from wynd.process._proc import require_tool, run
from wynd.process.errors import GitError, LoadError, ProcessNotFound, ToolMissing
from wynd.process.workspace import CommitTree, StepEntry, WorkingTree, find_workspace_root, load_workspace


def process_yaml(name: str, steps: str = "  s: { use: ./steps/s }") -> str:
    return f"kind: process\nname: {name}\nentry: s\nsteps:\n{steps}\n"


def proto_yaml(name: str) -> str:
    return (
        f"kind: proto_step\nname: {name}\ninstruction: Do {name}.\ninputs: {{x: string}}\noutputs: {{y: string}}\n"
        "examples:\n  - inputs: {x: a}\n    outputs: {y: b}\n"
    )


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def codes(diagnostics) -> list[tuple]:
    return sorted((d.code, d.file, d.process) for d in diagnostics)


# --- discovery ------------------------------------------------------------------------------------------------------

def test_flat_and_hierarchical_ids(make_repo):
    ws = make_repo("basic")
    workspace = load_workspace(ws)
    assert workspace.diagnostics == []
    assert workspace.process_ids() == ["finance/invoices", "intake"]
    assert workspace.processes["finance/invoices"].dir == "processes/finance/invoices"
    assert workspace.process_root_of("finance/invoices") == "processes"
    assert workspace.process_dir("intake") == ws / "processes" / "intake"
    assert workspace.root_steps == {
        "finance:extract/invoice": StepEntry(
            "finance:extract/invoice", "finance", "extract/invoice", "teams/finance/steps/extract/invoice", None
        ),
        "shared:greet": StepEntry(
            "shared:greet", "shared", "greet", "shared/steps/greet", "shared/steps/greet/proto.yaml"
        ),
    }
    with pytest.raises(ProcessNotFound, match="unknown process 'nope'"):
        workspace.process_dir("nope")


@pytest.mark.parametrize("marker", ["", "# just the marker\n", "{}\n"])
def test_default_roots_when_wynd_yaml_is_empty(make_repo, marker):
    ws = make_repo(files={"wynd.yaml": marker, "processes/p/process.yaml": process_yaml("p"),
                          "processes/p/proto/s.yaml": proto_yaml("s")})
    workspace = load_workspace(ws)
    assert workspace.config.process_roots == ["processes"]
    assert workspace.config.step_roots == {}
    assert workspace.process_ids() == ["p"]
    assert workspace.diagnostics == []


def test_markers_outside_every_root_are_ignored(make_repo):
    ws = make_repo(files={
        "wynd.yaml": "process_roots: [processes]\n",
        "processes/p/process.yaml": process_yaml("p"),
        "docs/sample/process.yaml": process_yaml("sample"),
        "docs/sample/steps/x/step.lock.yaml": "not even a lock",
    })
    workspace = load_workspace(ws)
    assert workspace.process_ids() == ["p"]
    assert workspace.diagnostics == []


def test_empty_root_on_disk_is_accepted(make_repo):
    ws = make_repo(files={"wynd.yaml": "process_roots: [processes]\n"})
    (ws / "processes").mkdir()
    assert load_workspace(ws).diagnostics == []


BASE = "process_roots: [processes]\nstep_roots: {shared: shared/steps}\n"
ROOTS = {"wynd.yaml": BASE, "processes/.keep": "", "shared/steps/.keep": ""}
LAYOUT_CASES = {
    "E103 missing root": (
        {"wynd.yaml": "process_roots: [processes, more]\n", "processes/p/process.yaml": process_yaml("p")},
        [("E103", "wynd.yaml", None)],
    ),
    "E103 invalid segment": (
        {"wynd.yaml": "process_roots: [processes]\nstep_roots: {s: .hidden/steps}\n",
         "processes/p/process.yaml": process_yaml("p"), ".hidden/steps/x/proto.yaml": proto_yaml("x")},
        [("E103", "wynd.yaml", None)],
    ),
    "E110 nested process": (
        {**ROOTS, "processes/a/process.yaml": process_yaml("a"),
         "processes/a/b/process.yaml": process_yaml("b")},
        [("E110", "processes/a/b/process.yaml", "a/b")],
    ),
    "E111 nested root package": (
        {**ROOTS, "shared/steps/x/proto.yaml": proto_yaml("x"),
         "shared/steps/x/y/proto.yaml": proto_yaml("y")},
        [("E111", "shared/steps/x/y/proto.yaml", None)],
    ),
    "E111 and E115 nested local package": (
        {**ROOTS, "processes/p/process.yaml": process_yaml("p"),
         "processes/p/steps/s/step.lock.yaml": "", "processes/p/steps/s/inner/step.lock.yaml": ""},
        [("E111", "processes/p/steps/s/inner/step.lock.yaml", "p"),
         ("E115", "processes/p/steps/s/inner/step.lock.yaml", "p")],
    ),
    "E112 duplicate id": (
        {"wynd.yaml": "process_roots: [p1, p2]\n", "p1/x/process.yaml": process_yaml("x"),
         "p2/x/process.yaml": process_yaml("x")},
        [("E112", "p2/x/process.yaml", "x")],
    ),
    "E114 process under step root": (
        {**ROOTS, "shared/steps/x/process.yaml": process_yaml("x")},
        [("E114", "shared/steps/x/process.yaml", None)],
    ),
    "E115 misplaced local package": (
        {**ROOTS, "processes/p/process.yaml": process_yaml("p"),
         "processes/p/lib/step.lock.yaml": "", "processes/p/lib/pyproject.toml": ""},
        [("E115", "processes/p/lib/step.lock.yaml", "p")],
    ),
    "E115 package outside any process": (
        {**ROOTS, "processes/loose/step.lock.yaml": ""},
        [("E115", "processes/loose/step.lock.yaml", None)],
    ),
    "E116 process id": (
        {**ROOTS, "processes/bad.name/process.yaml": process_yaml("bad")},
        [("E116", "processes/bad.name/process.yaml", None)],
    ),
    "E116 process at the root itself": (
        {**ROOTS, "processes/process.yaml": process_yaml("x")},
        [("E116", "processes/process.yaml", None)],
    ),
    "E116 root step id": (
        {**ROOTS, "shared/steps/my step/proto.yaml": proto_yaml("my_step")},
        [("E116", "shared/steps/my step/proto.yaml", None)],
    ),
    "E123 incomplete root package": (
        {**ROOTS, "shared/steps/x/pyproject.toml": ""},
        [("E123", "shared/steps/x/pyproject.toml", None)],
    ),
}


@pytest.mark.parametrize("case", LAYOUT_CASES)
def test_layout_diagnostics(make_repo, case):
    files, expected = LAYOUT_CASES[case]
    workspace = load_workspace(make_repo(files=files))
    assert codes(workspace.diagnostics) == sorted(expected)
    assert all(d.severity == "error" for d in workspace.diagnostics)


def test_layout_messages_name_both_sides(make_repo):
    files, _ = LAYOUT_CASES["E110 nested process"]
    [nested] = load_workspace(make_repo(files=files)).diagnostics
    assert nested.message == "process 'a/b' is nested inside process 'a'; processes are leaves and must not nest"
    files, _ = LAYOUT_CASES["E112 duplicate id"]
    [duplicate] = load_workspace(make_repo(files=files)).diagnostics
    assert duplicate.message == "process id 'x' is defined under both 'p1' and 'p2'"


def test_root_diagnostics_point_at_wynd_yaml(make_repo):
    files, _ = LAYOUT_CASES["E103 missing root"]
    [missing] = load_workspace(make_repo(files=files)).diagnostics
    assert (missing.loc, missing.line, missing.column) == (("process_roots", 1), 1, 28)
    assert missing.message == "process root 'more': does not exist"
    files, _ = LAYOUT_CASES["E103 invalid segment"]
    [invalid] = load_workspace(make_repo(files=files)).diagnostics
    assert invalid.loc == ("step_roots", "s")
    assert invalid.message == "step root '.hidden/steps': invalid segment '.hidden'"


def test_nested_and_duplicate_processes_stay_registered_once(make_repo):
    files, _ = LAYOUT_CASES["E110 nested process"]
    assert load_workspace(make_repo(files=files)).process_ids() == ["a", "a/b"]
    files, _ = LAYOUT_CASES["E112 duplicate id"]
    workspace = load_workspace(make_repo(files=files))
    assert workspace.processes["x"].root == "p1"


# --- load failures --------------------------------------------------------------------------------------------------

def test_missing_wynd_yaml_is_e100(make_repo):
    ws = make_repo(files={"processes/p/process.yaml": process_yaml("p")})
    with pytest.raises(LoadError) as err:
        load_workspace(ws)
    assert [d.code for d in err.value.diagnostics] == ["E100"]


def test_workspace_outside_git_is_e101(tmp_path):
    (tmp_path / "wynd.yaml").write_text("")
    with pytest.raises(LoadError) as err:
        load_workspace(tmp_path)
    [diagnostic] = err.value.diagnostics
    assert diagnostic.code == "E101"
    assert str(tmp_path) in diagnostic.message


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("process_roots: [processes, processes/sub]\n", "E-ROOTS"),
        ("step_roots: {process: steps}\n", "E-ROOTS"),
        ("step_roots: {vendor: {url: https://example.invalid/steps}}\n", "E-ROOTS"),
        ("process_roots: [../elsewhere]\n", "E-ROOTS"),
        ("step_roots: {Bad: steps}\n", "E-SCHEMA"),
        ("process_root: [processes]\n", "E-SCHEMA"),
        ("process_roots: [processes\n", "E-YAML"),
        ("- processes\n", "E-YAML-ROOT"),
    ],
)
def test_invalid_wynd_yaml_raises_spec_codes(make_repo, text, code):
    with pytest.raises(LoadError) as err:
        load_workspace(make_repo(files={"wynd.yaml": text}))
    assert {d.code for d in err.value.diagnostics} == {code}
    assert all(d.file == "wynd.yaml" for d in err.value.diagnostics)


def test_find_workspace_root(tmp_path, monkeypatch):
    (tmp_path / "ws" / "processes" / "p").mkdir(parents=True)
    (tmp_path / "ws" / "wynd.yaml").write_text("")
    assert find_workspace_root(tmp_path / "ws" / "processes" / "p") == tmp_path / "ws"
    assert find_workspace_root(tmp_path) is None
    monkeypatch.chdir(tmp_path / "ws" / "processes")
    assert find_workspace_root() == tmp_path / "ws"
    monkeypatch.setenv("WYND_WORKSPACE", str(tmp_path / "elsewhere"))
    assert find_workspace_root(tmp_path / "ws") == tmp_path / "elsewhere"


# --- trees ----------------------------------------------------------------------------------------------------------

def test_working_and_commit_trees_agree_after_commit(make_repo):
    ws = make_repo("basic")
    working, committed = WorkingTree(ws), CommitTree(ws, "HEAD")
    assert working.files() == committed.files()
    assert "processes/intake/steps/read/read.py" in working.files()
    for path in working.files():
        data = (ws / path).read_bytes()
        assert working.read_bytes(path) == committed.read_bytes(path) == data
        assert working.blob_id(path) == committed.blob_id(path) == git_blob(data)
    assert working.is_dir("processes/intake") and committed.is_dir("processes/intake")
    assert not working.is_dir("processes/nope") and not committed.is_dir("processes/intake/proto/read.yaml")
    with pytest.raises(FileNotFoundError):
        committed.read_bytes("nope.txt")
    with pytest.raises(FileNotFoundError):
        working.blob_id("nope.txt")


def test_working_tree_is_what_git_would_commit(make_repo, write_files, commit):
    ws = make_repo("basic")
    commit(ws, "ignore logs", {".gitignore": "*.log\n"})
    write_files(ws, {"notes.md": "draft", "debug.log": "noise", "processes/intake/proto/read.yaml": None})
    working, committed = WorkingTree(ws), CommitTree(ws, "HEAD")
    assert "notes.md" in working.files() and "notes.md" not in committed.files()
    assert "debug.log" not in working.files() and "debug.log" not in committed.files()
    assert "processes/intake/proto/read.yaml" not in working.files()
    assert "processes/intake/proto/read.yaml" in committed.files()


def test_state_dir_is_excluded_without_gitignore(make_repo, git, write_files):
    ws = make_repo("basic")
    assert not (ws / ".gitignore").exists()
    write_files(ws, {".wynd/traces/run_1.jsonl": "{}", ".wynd/tracked.txt": "x"})
    git(ws, "add", "-f", ".wynd/tracked.txt")
    git(ws, "commit", "-q", "-m", "a state file slipped in")
    for tree in (WorkingTree(ws), CommitTree(ws, "HEAD")):
        assert not any(path.startswith(".wynd") for path in tree.files())


def test_commit_tree_reads_history(make_repo, commit):
    ws = make_repo("basic")
    first = CommitTree(ws, "HEAD").sha
    second = commit(ws, "edit", {"processes/intake/notes.md": "v2"})
    old, new = CommitTree(ws, first), CommitTree(ws, "main")
    assert len(old.sha) == 40 and new.sha == second
    assert "processes/intake/notes.md" not in old.files()
    assert new.read_bytes("processes/intake/notes.md") == b"v2"
    with pytest.raises(GitError, match="'nope' is not a commit"):
        CommitTree(ws, "nope")


def test_subdirectory_workspace(make_repo, git):
    ws = make_repo("basic", subdir="examples/ws")
    repo = ws.parent.parent
    (repo / "README.md").write_text("repo readme")
    (repo / "other").mkdir()
    (repo / "other" / "process.yaml").write_text(process_yaml("other"))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "outside the workspace")
    working, committed = WorkingTree(ws), CommitTree(ws, "HEAD")
    assert working.files() == committed.files()
    assert "wynd.yaml" in working.files()
    assert not any(path.startswith(("examples/", "other/")) or path == "README.md" for path in working.files())
    for path in working.files():
        assert working.blob_id(path) == committed.blob_id(path)
    for workspace in (load_workspace(ws), load_workspace(ws, CommitTree(ws, "HEAD"))):
        assert workspace.process_ids() == ["finance/invoices", "intake"]
        assert workspace.diagnostics == []


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="git-lfs is not installed")
def test_lfs_cassettes_hash_to_their_pointer(make_repo, git, write_files, commit):
    ws = make_repo("basic")
    git(ws, "lfs", "install", "--local", "--skip-repo")
    commit(ws, "track cassettes", {".gitattributes": "**/cassettes/** filter=lfs diff=lfs merge=lfs -text\n"})
    recording = b'{"wynd_cassette": 1, "response": "' + b"x" * 200 + b'"}\n'
    path = "processes/intake/steps/read/cassettes/abc.json"
    write_files(ws, {path: recording})
    uncommitted = WorkingTree(ws).blob_id(path)
    commit(ws, "record")
    working, committed = WorkingTree(ws), CommitTree(ws, "HEAD")
    assert working.blob_id(path) == committed.blob_id(path) == uncommitted
    assert committed.blob_id(path) != git_blob(recording)
    assert committed.read_bytes(path).startswith(b"version https://git-lfs.github.com/spec/v1")
    assert working.read_bytes(path) == recording


def test_load_workspace_through_a_commit(make_repo, commit):
    ws = make_repo("basic")
    before = CommitTree(ws, "HEAD")
    commit(ws, "add a process", {"processes/extra/process.yaml": process_yaml("extra"),
                                 "processes/extra/proto/s.yaml": proto_yaml("s")})
    assert load_workspace(ws, before).process_ids() == ["finance/invoices", "intake"]
    assert load_workspace(ws).process_ids() == ["extra", "finance/invoices", "intake"]
    commit(ws, "drop the marker", {"wynd.yaml": None})
    with pytest.raises(LoadError) as err:
        load_workspace(ws, CommitTree(ws, "HEAD"))
    assert [d.code for d in err.value.diagnostics] == ["E100"]


def test_commit_tree_root_existence_ignores_the_disk(make_repo, commit):
    ws = make_repo(files={"wynd.yaml": "process_roots: [processes, later]\n",
                          "processes/p/process.yaml": process_yaml("p"), "processes/p/proto/s.yaml": proto_yaml("s")})
    old = CommitTree(ws, "HEAD")
    commit(ws, "add the root", {"later/q/process.yaml": process_yaml("q"), "later/q/proto/s.yaml": proto_yaml("s")})
    [missing] = load_workspace(ws, old).diagnostics
    assert (missing.code, missing.message) == ("E103", "process root 'later': does not exist")
    assert load_workspace(ws).diagnostics == []
    assert load_workspace(ws, CommitTree(ws, "HEAD")).diagnostics == []
    # An empty directory is accepted on disk (wynd init) but a commit cannot hold one.
    (ws / "empty").mkdir()
    commit(ws, "name an empty root", {"wynd.yaml": "process_roots: [processes, later, empty]\n"})
    assert load_workspace(ws).diagnostics == []
    [empty] = load_workspace(ws, CommitTree(ws, "HEAD")).diagnostics
    assert empty.message == "process root 'empty': does not exist"


# --- subprocess helpers ---------------------------------------------------------------------------------------------

def test_run_streams_stdout_and_stderr_lines(tmp_path):
    lines: list[str] = []
    script = "import sys; print('out 1'); print('err 1', file=sys.stderr); print(sys.stdin.read().upper())"
    out = run([sys.executable, "-c", script], cwd=tmp_path, log=lines.append, input="hello\n")
    assert out == "out 1\nHELLO\n\n"
    assert sorted(lines) == ["", "HELLO", "err 1", "out 1"]


def test_run_uses_cwd_and_env(tmp_path):
    script = "import os; print(os.getcwd()); print(os.environ.get('WYND_TEST_VALUE'))"
    out = run([sys.executable, "-c", script], cwd=tmp_path, env={"WYND_TEST_VALUE": "42"})
    assert out.splitlines() == [str(tmp_path.resolve()), "42"]


def test_run_failure_carries_stderr(tmp_path):
    script = "import sys; print('partial'); sys.exit('boom')"
    with pytest.raises(subprocess.CalledProcessError) as err:
        run([sys.executable, "-c", script], cwd=tmp_path)
    assert (err.value.returncode, err.value.stdout, err.value.stderr) == (1, "partial\n", "boom\n")
    assert run([sys.executable, "-c", script], cwd=tmp_path, check=False) == "partial\n"


def test_run_large_input_and_output_do_not_deadlock(tmp_path):
    data = "x" * 1_000_000
    out = run([sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"], cwd=tmp_path, input=data)
    assert out == data


def test_run_missing_executable_is_tool_missing(tmp_path):
    with pytest.raises(ToolMissing, match="'wynd-no-such-tool' is required but was not found"):
        run(["wynd-no-such-tool", "--version"], cwd=tmp_path)
    with pytest.raises(FileNotFoundError):
        run([sys.executable, "-c", "pass"], cwd=tmp_path / "missing")


def test_require_tool(monkeypatch, tmp_path):
    assert Path(require_tool("git")).name == "git"
    monkeypatch.setenv("WYND_UV", sys.executable)
    assert require_tool("uv") == sys.executable
    monkeypatch.setenv("WYND_UV", str(tmp_path / "no-uv"))
    with pytest.raises(ToolMissing, match="WYND_UV=.*does not name an executable"):
        require_tool("uv")
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(ToolMissing, match=r"'docker' is required but was not found: install Docker"):
        require_tool("docker")

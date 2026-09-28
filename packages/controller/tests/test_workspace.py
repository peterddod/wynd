"""`init_workspace`, `new_process_files` and `ProcessService.new` (PLAN §8.1 workspace row; `$DRAFTS/06 §5.4`)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from wynd.controller.errors import Conflict, Invalid
from wynd.controller.workspace import GITATTRIBUTES_LINES, GITIGNORE_LINES, init_workspace, new_process_files
from wynd.process.workspace import load_workspace
from wynd.spec.process_doc import ProcessDoc
from wynd.spec.proto_step import ProtoStep


def committed_files(git, repo: Path, ref: str = "HEAD") -> list[str]:
    return sorted(git(repo, "show", "--name-only", "--format=", ref).split())


def test_init_outside_a_repository_creates_the_repository_and_commits(tmp_path, git, fake_git_lock):
    root = tmp_path / "ws"
    result = init_workspace(root)

    assert result.root == str(root)
    assert result.created == ["wynd.yaml", "processes/.gitkeep", "shared/steps/.gitkeep", ".gitignore",
                              ".gitattributes"]
    assert (root / ".git").is_dir()
    assert git(root, "symbolic-ref", "--short", "HEAD").strip() == "main"
    assert result.commit == git(root, "rev-parse", "HEAD").strip()
    assert git(root, "log", "-1", "--format=%s").strip() == "chore(wynd): init workspace"
    assert committed_files(git, root) == sorted(result.created)
    assert (root / ".gitignore").read_text().splitlines() == list(GITIGNORE_LINES)
    assert (root / ".gitattributes").read_text().splitlines() == list(GITATTRIBUTES_LINES)
    assert fake_git_lock.instances[0].path == root / ".wynd" / "locks" / "git.lock"
    assert fake_git_lock.instances[0].entered == 1

    ws = load_workspace(root)                                  # a loadable, empty workspace
    assert ws.process_ids() == [] and ws.diagnostics == []
    assert list(ws.config.step_roots) == ["shared"]
    assert git(root, "status", "--porcelain") == ""


def test_init_appends_only_missing_ignore_lines(tmp_path, git, fake_git_lock):
    root = tmp_path / "ws"
    root.mkdir()
    (root / ".gitignore").write_text("node_modules/\n.env")                # no trailing newline
    (root / ".gitattributes").write_text(GITATTRIBUTES_LINES[0] + "\n")
    result = init_workspace(root)
    assert (root / ".gitignore").read_text() == "node_modules/\n.env\n.wynd/\n__pycache__/\n.pytest_cache/\n"
    assert (root / ".gitattributes").read_text() == GITATTRIBUTES_LINES[0] + "\n"
    assert ".gitattributes" not in result.created and ".gitignore" in result.created


def test_init_inside_an_existing_repository_commits_only_what_it_created(git_repo, git, fake_git_lock):
    (git_repo / "notes.txt").write_text("unrelated, uncommitted\n")
    root = git_repo / "tools" / "wynd"
    result = init_workspace(root)

    assert not (root / ".git").exists()                                     # no nested repository
    assert git(git_repo, "log", "--format=%s").splitlines() == ["chore(wynd): init workspace", "initial"]
    assert committed_files(git, git_repo) == sorted(f"tools/wynd/{p}" for p in result.created)
    assert git(git_repo, "status", "--porcelain").splitlines() == ["?? notes.txt"]


def test_init_without_commit_leaves_the_files_uncommitted(tmp_path, git, fake_git_lock):
    root = tmp_path / "ws"
    result = init_workspace(root, commit=False)
    assert result.commit is None
    assert (root / "wynd.yaml").is_file()
    assert git(root, "status", "--porcelain", "--untracked-files=all").strip() != ""
    assert fake_git_lock.instances == []


def test_init_refuses_an_existing_workspace(tmp_path, fake_git_lock):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "wynd.yaml").write_text("process_roots: [processes]\n")
    with pytest.raises(Conflict, match="already a wynd workspace"):
        init_workspace(root)


def test_new_process_files_are_valid_documents():
    files = new_process_files("finance/invoices", goal="Pay: every 'invoice' on time")
    assert sorted(files) == ["process.yaml", "proto/first.yaml"]
    doc = ProcessDoc.model_validate(yaml.safe_load(files["process.yaml"]))
    assert (doc.name, doc.goal, doc.entry) == ("invoices", "Pay: every 'invoice' on time", "first")
    assert doc.steps["first"].use == "./steps/first"
    proto = ProtoStep.model_validate(yaml.safe_load(files["proto/first.yaml"]))
    assert proto.name == "first" and proto.examples == []
    assert "TODO" in yaml.safe_load(new_process_files("x", goal=None)["process.yaml"])["goal"]


def test_new_scaffolds_validates_and_commits(controller, workspace, git):
    summary = controller.processes.new("finance/invoices", goal="Pay invoices")

    assert (summary.id, summary.name, summary.goal, summary.path) == (
        "finance/invoices", "invoices", "Pay invoices", "processes/finance/invoices")
    assert summary.error is None and summary.status.design is True            # proto-only step
    assert summary.status.head is not None and summary.status.dirty == []
    assert git(workspace, "log", "-1", "--format=%s").strip() == "design(finance/invoices): new process"
    assert committed_files(git, workspace) == ["processes/finance/invoices/process.yaml",
                                               "processes/finance/invoices/proto/first.yaml"]
    assert controller.ctx.git_lock.entered == 1
    report = controller.processes.validate("finance/invoices")                # the real validator
    assert report.ok, report.issues


def test_new_without_commit(controller, workspace, git):
    summary = controller.processes.new("scratch", commit=False)
    assert summary.status.head is None and summary.status.design is True
    assert summary.status.dirty == ["processes/scratch/process.yaml", "processes/scratch/proto/first.yaml"]
    assert git(workspace, "log", "-1", "--format=%s").strip() == "initial"


@pytest.mark.parametrize("pid", ["Bad", "a//b", "../x", "x/", "a b", "x/status", "9lives", "with-dash"])
def test_new_rejects_bad_ids(controller, pid):
    with pytest.raises(Invalid):
        controller.processes.new(pid)


def test_new_rejects_an_unknown_root(controller):
    with pytest.raises(Invalid, match="not a process root"):
        controller.processes.new("x", root="elsewhere")


@pytest.mark.parametrize(("pid", "match"), [("p1", "already exists"), ("p1/inner", "nested inside process 'p1'")])
def test_new_rejects_existing_and_nested_processes(controller, pid, match):
    with pytest.raises(Conflict, match=match):
        controller.processes.new(pid)

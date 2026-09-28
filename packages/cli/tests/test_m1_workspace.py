"""`wynd init, new, validate [--sync-interfaces], status` and the root options (PLAN §9, §3.22; `$DRAFTS/06 §9.2`)
over a real controller."""

from __future__ import annotations

import json

import pytest

P1_YAML = "processes/p1/process.yaml"


@pytest.fixture
def ctl(workspace, make_controller, use_controller):
    return use_controller(make_controller(workspace))


# --- init / new -------------------------------------------------------------------------------------------------------

def test_init_outside_a_repository_creates_and_commits(tmp_path, cli, git):
    target = tmp_path / "ws"
    result = cli("init", target)
    lines = result.stdout.splitlines()
    assert lines[0] == f"initialised workspace {target}"
    assert {"  created wynd.yaml", "  created .gitignore", "  created .gitattributes"} <= set(lines)
    assert lines[-1].startswith("committed ") and len(lines[-1].split()[1]) == 7
    assert git(target, "log", "--format=%s").strip() == "chore(wynd): init workspace"
    assert git(target, "status", "--porcelain") == ""

    again = cli("init", target, code=3)
    assert "already a wynd workspace" in again.stderr and again.stdout == ""


def test_init_inside_a_repository_without_committing_as_json(tmp_path, cli, git):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    result = cli("init", repo / "sub", "--no-commit", "--json")
    doc = json.loads(result.stdout)
    assert doc["root"] == str(repo / "sub") and doc["commit"] is None and "wynd.yaml" in doc["created"]
    assert (repo / "sub" / "wynd.yaml").is_file()


def test_new_creates_a_valid_process_and_commits_it(ctl, workspace, cli, git):
    result = cli("new", "finance/invoices", "--goal", "Pay supplier invoices.")
    assert result.stdout.splitlines() == [
        "created processes/finance/invoices/process.yaml",
        "created processes/finance/invoices/proto/first.yaml",
        "next: edit processes/finance/invoices/proto/first.yaml, then wynd validate finance/invoices",
    ]
    assert git(workspace, "log", "-1", "--format=%s").strip() == "design(finance/invoices): new process"
    assert "Pay supplier invoices." in (workspace / "processes/finance/invoices/process.yaml").read_text()
    assert cli("validate", "finance/invoices").stdout.splitlines()[-1] == "0 errors, 1 warnings"   # no examples yet

    assert "already exists" in cli("new", "finance/invoices", code=3).stderr
    assert "reserved segment 'status'" in cli("new", "x/status", code=2).stderr


def test_new_without_commit_as_json(ctl, workspace, cli, git):
    doc = json.loads(cli("new", "drafts", "--no-commit", "--json").stdout)
    assert (doc["id"], doc["path"]) == ("drafts", "processes/drafts")
    assert git(workspace, "status", "--porcelain", "--untracked-files=all").split() == [
        "??", "processes/drafts/process.yaml", "??", "processes/drafts/proto/first.yaml"]


# --- validate ---------------------------------------------------------------------------------------------------------

def test_validate_a_clean_process(ctl, cli):
    result = cli("validate", "p1")
    assert result.stdout == "0 errors, 0 warnings\n"
    assert json.loads(cli("validate", "p1", "--json").stdout) == {"ok": True, "issues": []}


def test_validate_errors_exit_1(ctl, workspace, cli, write_files):
    text = (workspace / P1_YAML).read_text().replace("to: count\n", "to: counter\n")
    write_files(workspace, {P1_YAML: text})
    result = cli("validate", "p1", code=1)
    lines = result.stdout.splitlines()
    unknown = next(line for line in lines if "E-STEP-UNKNOWN" in line)
    assert unknown.startswith(f"ERROR  E-STEP-UNKNOWN  {P1_YAML} edges[0].to[0].step  ")
    assert "counter" in unknown
    assert any(line.startswith("ERROR  E209  ") for line in lines)
    errors = sum(line.startswith("ERROR") for line in lines)
    assert lines[-1] == f"{errors} errors, 0 warnings"

    doc = json.loads(cli("validate", "p1", "--json", code=1).stdout)
    assert doc["ok"] is False and "E-STEP-UNKNOWN" in {issue["code"] for issue in doc["issues"]}


def test_validate_every_process(ctl, workspace, cli, write_files):
    result = cli("validate")
    assert result.stdout.splitlines() == [
        "p1: ok",
        "p2: ok",
        "p3:",
        "  WARN   W-PROTO-NO-EXAMPLES  processes/p3/proto/read.yaml examples  no examples: the compiler needs "
        "examples to generate tests",
        "parent: ok",
        "0 errors, 1 warnings",
    ]
    assert set(json.loads(cli("validate", "--json").stdout)) == {"p1", "p2", "p3", "parent"}

    write_files(workspace, {"processes/p2/process.yaml": "kind: process\nname: [unclosed\n"})
    broken = cli("validate", code=1)
    assert "p2:" in broken.stdout.splitlines() and "E-YAML" in broken.stdout


def test_validate_sync_interfaces(ctl, workspace, cli, git, write_files):
    result = cli("validate", "p1", "--sync-interfaces")
    assert "p1: interface snapshots in sync" in result.stderr
    assert result.stdout == "0 errors, 0 warnings\n"

    lock = workspace / "processes/p1/steps/upper/step.lock.yaml"
    text = lock.read_text()
    snapshot = "      text:\n        title: Text\n        type: string\n    required:\n    - text\n    title: Input\n"
    assert snapshot in text
    write_files(workspace, {"processes/p1/steps/upper/step.lock.yaml": text.replace(snapshot, snapshot.replace(
        "title: Text", "title: Drifted"))})
    fixed = cli("validate", "p1", "--sync-interfaces", "--json")
    assert "updated processes/p1/steps/upper/step.lock.yaml" in fixed.stderr
    assert json.loads(fixed.stdout)["ok"] is True
    assert git(workspace, "status", "--porcelain") == ""                # the snapshot is back to the committed one

    every = cli("validate", "--sync-interfaces")
    assert every.stderr.splitlines() == [f"{pid}: interface snapshots in sync" for pid in ("p1", "p2", "p3", "parent")]


# --- status -----------------------------------------------------------------------------------------------------------

def test_status_table(ctl, workspace, cli, git, write_files):
    head = git(workspace, "rev-parse", "HEAD").strip()[:7]
    lines = cli("status").stdout.splitlines()
    assert lines[0].split() == ["PROCESS", "HEAD", "DIRTY", "DESIGN", "COMPILED", "BUILT", "RELEASED", "TESTS"]
    rows = {line.split()[0]: line.split() for line in lines[1:]}
    assert rows["p1"] == ["p1", head, "-", "-", "-", "-", "-", "unknown"]
    assert rows["p2"] == ["p2", head, "-", "yes", "-", "-", "-", "unknown"]

    write_files(workspace, {"processes/p1/notes.md": "dirty\n",
                            "processes/p3/process.yaml": "kind: process\nname: [unclosed\n"})
    result = cli("status")
    rows = {line.split()[0]: line.split() for line in result.stdout.splitlines()[1:]}
    assert rows["p1"][2] == "yes" and rows["p3"] == ["p3", "-", "-", "-", "-", "-", "-", "error"]
    assert result.stderr.startswith("p3: ") and "E-YAML" in result.stderr

    doc = json.loads(cli("status", "--json").stdout)
    assert [item["id"] for item in doc["items"]] == ["p1", "p2", "p3", "parent"]


def test_status_of_one_process(ctl, workspace, cli, git, write_files):
    head = git(workspace, "rev-parse", "HEAD").strip()[:7]
    assert cli("status", "p2").stdout.splitlines() == [
        "process       p2",
        f"head          {head}  initial",
        "design        yes",
        "compiled      no",
        "built         no",
        "released      no",
        "tests         unknown",
        "design steps  tag",
    ]
    write_files(workspace, {"processes/p1/notes.md": "dirty\n"})
    assert "dirty         processes/p1/notes.md" in cli("status", "p1").stdout.splitlines()
    doc = json.loads(cli("status", "p1", "--json").stdout)
    assert (doc["design"], doc["tests"], doc["dirty"], doc["head"]["short"]) == (
        False, "unknown", ["processes/p1/notes.md"], head)
    assert "unknown process 'nope'" in cli("status", "nope", code=3).stderr


def test_status_shows_the_build_at_head_and_releases(ctl, workspace, cli, commit):
    from datetime import UTC, datetime

    from wynd.process.artefacts import BuildInfo
    from wynd.spec.env_manifest import EnvManifest

    released = commit(workspace, "unrelated", {"README.md": "outside every closure\n"})
    ctl.ctx.docs.put("releases", "rel_1", {"id": "rel_1", "process_id": "p1", "commit": released, "image": "img",
                                           "trigger": {"kind": "manual"}, "env": {}, "enabled": True})
    head = commit(workspace, "notes", {"processes/p1/notes.md": "notes\n"})
    staged = ctl.ctx.state_dir / "tmp" / "stage"
    staged.mkdir(parents=True)
    ctl.ctx.artefacts.put_build(staged, BuildInfo(
        process="p1", commit=head, source_sha=head, job_id=None, process_hash="sha256:" + "0" * 64,
        image=f"wynd/p1:{head[:12]}", image_id=None, image_digest=None, base={},
        manifest=EnvManifest(process="p1", vars=[]), created_at=datetime(2026, 9, 22, tzinfo=UTC), dir=""))

    lines = cli("status", "p1").stdout.splitlines()
    assert "built         yes" in lines and "released      yes" in lines
    assert f"build         wynd/p1:{head[:12]} @{head[:7]}" in lines
    assert f"release       rel_1 manual @{released[:7]} behind=1 serving" in lines
    row = next(line for line in cli("status").stdout.splitlines() if line.startswith("p1 "))
    assert row.split()[5:] == ["yes", "yes", f"({released[:7]},", "1", "behind)", "unknown"]     # BUILT, RELEASED


def test_status_after_tests_shows_compiled(ctl, cli):
    cli("test", "p1")
    lines = cli("status", "p1").stdout.splitlines()
    assert "compiled      yes" in lines and "tests         passed" in lines


# --- root options and a real Controller.open --------------------------------------------------------------------------

def test_workspace_option_opens_a_real_controller(workspace, tmp_path, cli, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli("-C", workspace, "validate", "p1").stdout == "0 errors, 0 warnings\n"
    assert cli("-C", workspace / "processes" / "p1", "status", "p1").stdout.startswith("process       p1\n")
    monkeypatch.setenv("WYND_WORKSPACE", str(workspace))
    assert cli("validate", "p2").stdout == "0 errors, 0 warnings\n"


def test_cwd_inside_the_workspace(workspace, cli, monkeypatch):
    monkeypatch.chdir(workspace / "processes")
    assert "p1" in cli("status").stdout


def test_not_a_workspace_exits_3(tmp_path, cli, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = cli("status", code=3)
    assert result.stderr.splitlines() == [
        f"error: no wynd.yaml found in {tmp_path} or any parent directory",
        "hint: create a workspace with `wynd init`",
    ]
    assert result.stdout == ""


def test_version_and_help(cli):
    assert cli("--version").stdout == "wynd 0.1.0\n"
    for command in ("init", "new", "validate", "status", "test", "run", "trace", "env", "mcp", "provider",
                    "registry", "jobs"):
        assert command in cli("--help").stdout

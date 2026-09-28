"""Error mapping in the root group (PLAN §3.22 exit codes; `$DRAFTS/06 §9.1`): a controller error prints
`error: <message>` (+ `hint: <hint>`) on stderr and exits with its `exit`; under `--json` stdout carries exactly one
`{"error": …}` document; Ctrl-C exits 130; click usage errors exit 2."""

from __future__ import annotations

import json

import pytest

from wynd.controller.errors import (
    Conflict,
    DetachedHead,
    DirtyTree,
    EnvMissing,
    Invalid,
    NotBuilt,
    Unavailable,
    ValidationFailed,
    VersionMismatch,
)


@pytest.fixture
def ctl(workspace, make_controller, use_controller):
    return use_controller(make_controller(workspace))


def raising(error: BaseException):
    def fail(*args, **kwargs):
        raise error

    return fail


@pytest.mark.parametrize(("error", "code"), [
    (ValidationFailed("process 'p1' has validation errors"), 1),
    (Invalid("bad input"), 2),
    (Conflict("conflict"), 3),
    (DetachedHead("HEAD is detached"), 3),
    (NotBuilt("not built"), 3),
    (EnvMissing("env missing"), 3),
    (Unavailable("docker is not running"), 3),
    (VersionMismatch("version mismatch"), 3),
])
def test_every_controller_error_exits_with_its_code(ctl, cli, monkeypatch, error, code):
    monkeypatch.setattr(ctl.processes, "validate", raising(error))
    result = cli("validate", "p1", code=code)
    assert result.stderr == f"error: {error.message}\n" and result.stdout == ""


def test_hint_details_and_the_json_error_document(ctl, cli, monkeypatch):
    error = DirtyTree("the workspace has uncommitted changes", details={"paths": ["a.txt", "b/c.txt"]},
                      hint="commit them")
    monkeypatch.setattr(ctl.processes, "validate", raising(error))
    result = cli("validate", "p1", "--json", code=3)
    assert result.stderr.splitlines() == ["error: the workspace has uncommitted changes", "  a.txt", "  b/c.txt",
                                          "hint: commit them"]
    assert json.loads(result.stdout) == {"error": {"code": "dirty_tree", "message": error.message,
                                                   "details": {"paths": ["a.txt", "b/c.txt"]}, "hint": "commit them"}}
    plain = cli("validate", "p1", code=3)
    assert plain.stdout == ""


def test_ctrl_c_exits_130(ctl, cli, monkeypatch):
    monkeypatch.setattr(ctl.processes, "validate", raising(KeyboardInterrupt()))
    assert cli("validate", "p1", code=130).stderr == "interrupted\n"


def test_ctrl_c_while_waiting_for_a_job_says_how_to_follow_it(workspace, make_controller, use_controller, cli,
                                                             monkeypatch, job_handlers):
    ctl = use_controller(make_controller(workspace, handlers=job_handlers))
    monkeypatch.setattr(ctl.jobs, "wait", raising(KeyboardInterrupt()))
    result = cli("test", "p1", "--live", code=130)
    job_id = result.stderr.split()[1]
    assert result.stderr.splitlines()[1:] == [f"job continues: wynd jobs logs {job_id} -f", "interrupted"]


def test_usage_errors_exit_2(ctl, cli):
    assert "No such command 'nope'" in cli("nope", code=2).stderr
    assert "Missing argument" in cli("env", "check", code=2).stderr
    assert "--limit" in cli("jobs", "list", "--limit", "0", code=2).stderr


def test_other_exceptions_are_not_swallowed(ctl, cli, monkeypatch):
    monkeypatch.setattr(ctl.processes, "validate", raising(RuntimeError("boom")))
    result = cli("validate", "p1", code=1)
    assert isinstance(result.exception, RuntimeError)

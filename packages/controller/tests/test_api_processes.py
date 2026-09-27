"""Process routes (PLAN §3.21 incl. amendment 14; `$DRAFTS/07 §12.1` routes 3-9, 13, 14, §12.3; `$DRAFTS/06 §8.3`
(+) process routes) over the `ws_basic` fixture."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from support.ctl_api_client import api_client
from support.ctl_rel_fakes import put_build
from wynd.controller.status import process_head
from wynd.process.testing import SuiteResult, TestReport
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.fragments import EnvVar

P1 = "processes/p1/process.yaml"


@pytest.fixture
def client(controller):
    return api_client(controller)


def dump(models) -> list:
    return [m.model_dump(mode="json") for m in models]


# --- listing, search, create, get -------------------------------------------------------------------------------------

def test_list_and_search(client, controller):
    listed = client.get("/api/processes").json()["processes"]
    assert [p["id"] for p in listed] == ["p1", "p2", "parent"]
    assert listed == dump(controller.processes.list())

    found = client.get("/api/processes", params={"q": "upper"}).json()["processes"]
    assert [p["id"] for p in found] == ["p1"] and found[0]["matches"]
    flagged = client.get("/api/processes", params={"flags": "design,compiled"}).json()["processes"]
    assert flagged == dump(controller.processes.list("", ["design", "compiled"]))


def test_an_unknown_status_flag_is_invalid(client):
    response = client.get("/api/processes", params={"flags": "design,shipped"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid" and "shipped" in response.json()["error"]["message"]


def test_create_a_nested_process_and_reach_it_by_its_id(client, workspace, git):
    response = client.post("/api/processes", json={"id": "finance/invoices", "goal": "Pay invoices."})
    assert response.status_code == 201
    assert (response.json()["id"], response.json()["path"]) == ("finance/invoices", "processes/finance/invoices")
    assert git(workspace, "log", "-1", "--format=%s").strip() == "design(finance/invoices): new process"

    assert client.get("/api/processes/finance/invoices").json()["goal"] == "Pay invoices."
    design = client.get("/api/processes/finance/invoices/design").json()
    assert design["process_id"] == "finance/invoices"
    assert design["process_file"]["path"] == "processes/finance/invoices/process.yaml"
    assert client.get("/api/processes/finance/invoices/status").status_code == 200

    again = client.post("/api/processes", json={"id": "finance/invoices"})
    assert (again.status_code, again.json()["error"]["code"]) == (409, "conflict")


def test_an_unknown_process_is_404(client):
    for path in ("/api/processes/nope", "/api/processes/nope/design", "/api/processes/nope/status",
                 "/api/processes/a/b/interface"):
        response = client.get(path)
        assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found"), path


# --- design (07 §12.3) ------------------------------------------------------------------------------------------------

def test_save_and_commit_a_design(client, workspace, git):
    design = client.get("/api/processes/p1/design").json()
    doc = {**design["process_file"]["doc"], "goal": "Shout and count."}
    body = {"writes": [{"path": P1, "base_revision": design["process_file"]["revision"], "doc": doc}],
            "commit": {"reason": "blur", "summary": "new goal"}}
    response = client.post("/api/processes/p1/design", json=body)

    assert response.status_code == 200
    result = response.json()
    assert result["commit"]["message"] == "design(p1): new goal"
    assert result["head"] == result["commit"]["sha"] == git(workspace, "rev-parse", "HEAD").strip()
    assert result["files"][0]["path"] == P1 and "goal: Shout and count." in result["files"][0]["yaml"]
    assert result["validation"]["ok"] is True and set(result["steps"]) == {"upper", "count"}
    assert "Shout and count." in (workspace / P1).read_text()
    assert "Wynd-Origin: web" in git(workspace, "log", "-1", "--format=%B")


def test_a_stale_revision_is_409_and_writes_nothing(client, workspace):
    before = (workspace / P1).read_text()
    body = {"writes": [{"path": P1, "base_revision": "sha256:" + "0" * 64, "doc": {"kind": "process"}}]}
    response = client.post("/api/processes/p1/design", json=body)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "revision_conflict" and error["details"]["path"] == P1
    assert error["details"]["current_revision"].startswith("sha256:")
    assert (workspace / P1).read_text() == before


def test_a_path_outside_the_design_scope_is_403(client):
    body = {"writes": [{"path": "processes/p2/process.yaml", "base_revision": None, "doc": {}}]}
    response = client.post("/api/processes/p1/design", json=body)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "out_of_scope")


def test_a_design_locked_by_a_chat_turn_is_423(client, controller):
    controller.design.lock("p1", "chat_1", "turn_1")
    assert client.get("/api/processes/p1/design").json()["locked_by"] == {"chat_id": "chat_1", "turn_id": "turn_1"}
    design = client.get("/api/processes/p1/design").json()
    body = {"writes": [{"path": P1, "base_revision": design["process_file"]["revision"],
                        "doc": design["process_file"]["doc"]}]}
    response = client.post("/api/processes/p1/design", json=body)
    assert response.status_code == 423
    assert response.json()["error"]["details"] == {"chat_id": "chat_1", "turn_id": "turn_1"}


def test_validate_an_expression_of_the_unsaved_document(client):
    doc = client.get("/api/processes/p1/design").json()["process_file"]["doc"]
    body = {"process_id": "p1", "process": doc, "loc": ["edges", 0, "to", 0, "with", "text"],
            "expr": "steps.uper.outputs.text", "scope": True}
    check = client.post("/api/expressions/validate", json=body).json()
    assert check["ok"] is False
    assert check["errors"] == [{"message": "unknown step 'uper' (did you mean 'upper'?)", "start": 0, "end": 23}]
    assert "steps.upper.outputs.text" in check["scope"]
    assert client.post("/api/expressions/validate", json={**body, "expr": "steps.upper.outputs.text",
                                                          "scope": False}).json() == {
        "ok": True, "errors": [], "warnings": [], "scope": None}


# --- reads ------------------------------------------------------------------------------------------------------------

def test_the_step_catalog(client):
    assert client.get("/api/steps").json() == {"steps": [{
        "use": "shared:normalise", "root": "shared", "path": "normalise", "kind": "deterministic",
        "phase": "compiled", "instruction": "Trim and lower-case the name.", "used_by": ["p2"]}]}


def test_builds_newest_first_against_the_head(client, controller):
    assert client.get("/api/processes/p1/builds").json() == {"builds": []}
    head = process_head(controller.ctx, "p1")[0]
    put_build(controller, "p1", head, EnvManifest(process="p1", commit=head, vars=[EnvVar(name="RECORDS_DIR")]))
    builds = client.get("/api/processes/p1/builds").json()["builds"]
    assert [(b["commit"], b["at_head"], b["behind"]) for b in builds] == [(head, True, 0)]
    assert [v["name"] for v in builds[0]["env"]] == ["RECORDS_DIR"]


def test_the_interface_of_the_working_tree_or_a_commit(client, workspace, git):
    head = git(workspace, "rev-parse", "HEAD").strip()
    iface = client.get("/api/processes/p1/interface").json()
    assert iface["commit"] is None and set(iface["outputs"]) == {"done", "empty"}
    assert iface["inputs"]["properties"] == {"text": {"type": "string"}}
    at_commit = client.get("/api/processes/p1/interface", params={"commit": head}).json()
    assert at_commit["commit"] == head and at_commit["examples"] == iface["examples"]
    missing = client.get("/api/processes/p1/interface", params={"commit": "f" * 40})
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "not_found")


def test_status_reports_dirt_without_changing_a_flag(client, workspace):
    clean = client.get("/api/processes/p1/status").json()
    assert clean["dirty"] == [] and clean["tests"] == "unknown"
    (workspace / P1).write_text((workspace / P1).read_text() + "\n# edited\n")
    dirty = client.get("/api/processes/p1/status").json()
    assert dirty["dirty"] == [P1]
    assert {k: dirty[k] for k in ("design", "compiled", "built", "released")} == {
        k: clean[k] for k in ("design", "compiled", "built", "released")}


def test_validate_one_process_and_the_workspace(client, workspace):
    assert client.post("/api/processes/p1/validate").json() == {"ok": True, "issues": []}
    (workspace / P1).write_text((workspace / P1).read_text().replace("entry: upper", "entry: nowhere"))
    report = client.post("/api/processes/p1/validate").json()
    assert report["ok"] is False and report["issues"]
    reports = client.post("/api/workspace/validate").json()["reports"]
    assert set(reports) == {"p1", "p2", "parent"} and reports["p1"] == report


def test_history_of_the_closure(client, workspace, commit):
    sha = commit(workspace, "tweak p1", {P1: (workspace / P1).read_text() + "\n"})
    commits = client.get("/api/processes/p1/history").json()["commits"]
    assert [(c["sha"], c["subject"]) for c in commits][0] == (sha, "tweak p1")
    assert [c["subject"] for c in commits] == ["tweak p1", "initial"]
    assert len(client.get("/api/processes/p1/history", params={"limit": 1}).json()["commits"]) == 1


def test_read_a_closure_file_by_query_parameter(client, workspace):
    response = client.get("/api/processes/p1/files", params={"path": "processes/p1/proto/upper.yaml"})
    assert response.status_code == 200
    content = response.json()
    assert content["path"] == "processes/p1/proto/upper.yaml" and content["revision"].startswith("sha256:")
    assert content["content"] == (workspace / "processes/p1/proto/upper.yaml").read_text()
    for path in ("processes/p2/process.yaml", "../outside", "processes/p1/nope.yaml"):
        response = client.get("/api/processes/p1/files", params={"path": path})
        assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found"), path


def test_the_env_report_says_where_each_value_comes_from(workspace, make_controller):
    missing = api_client(make_controller(workspace, env={"RECORDS_DIR": None})).get("/api/processes/p1/env").json()
    assert (missing["process"], missing["manifest"], missing["commit"], missing["ok"]) == ("p1", "assembled", None,
                                                                                            False)
    row = next(v for v in missing["vars"] if v["name"] == "RECORDS_DIR")
    assert (row["required"], row["set"], row["source"]) == (True, False, "missing")
    assert row["used_by"] == ["edge:p1:upper.done[0].dest"]

    from_env = api_client(make_controller(workspace, env={"RECORDS_DIR": "/tmp/r"})).get("/api/processes/p1/env")
    row = next(v for v in from_env.json()["vars"] if v["name"] == "RECORDS_DIR")
    assert (from_env.json()["ok"], row["set"], row["source"]) == (True, True, "env")

    (workspace / ".env").write_text("RECORDS_DIR=/tmp/dotenv\n")
    dotenv = api_client(make_controller(workspace, env={"RECORDS_DIR": None})).get("/api/processes/p1/env").json()
    row = next(v for v in dotenv["vars"] if v["name"] == "RECORDS_DIR")
    assert (dotenv["ok"], row["set"], row["source"]) == (True, True, "dotenv")


def test_replay_tests_run_synchronously(client, controller, monkeypatch):
    at = datetime(2026, 9, 27, 12, tzinfo=UTC)
    report = TestReport(process="p1", commit="c" * 40, process_hash="sha256:" + "1" * 64, mode="replay", passed=True,
                        suites=[SuiteResult(subject="process:p1", hash="sha256:" + "2" * 64, passed=True,
                                            counts={"passed": 2}, cases=[])],
                        started_at=at, finished_at=at, recorded=True)
    calls = []
    monkeypatch.setattr(controller.processes, "test", lambda pid: calls.append(pid) or report)
    response = client.post("/api/processes/p1/test")
    assert response.status_code == 200 and calls == ["p1"]
    assert response.json() == report.model_dump(mode="json")

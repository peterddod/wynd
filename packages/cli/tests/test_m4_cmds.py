"""`wynd serve-api`, `wynd search` and `wynd release …` (CLI-M4; PLAN §9, §3.21, §3.22; `$DRAFTS/06 §9.2`).

`serve-api` runs with `wynd.controller.api.serve` replaced by a recorder (CTL-API owns the server). `search` runs over
a real controller on the fixture workspace. `release` runs over a real `ReleaseService` whose serving and trigger
backends are the CTL-REL fakes and whose run API is the CTL-M2 `FakeRunApi`; builds are stored with `put_build`.
"""

from __future__ import annotations

import json

import pytest

from support.ctl_m2_runapi import FakeRunApi
from support.ctl_rel_fakes import IMAGE, FakeServing, FakeTriggers, install_backends, put_build
from wynd.controller.releases.service import ReleaseService
from wynd.controller.status import process_head
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.fragments import EnvVar

# --- serve-api --------------------------------------------------------------------------------------------------------

DEFAULTS = {"host": "127.0.0.1", "port": 8780, "web_dist": None, "api_token": None, "cors_origins": None,
            "scheduler": True, "log_level": "info"}


@pytest.fixture
def served(monkeypatch) -> list:
    calls = []
    monkeypatch.setattr("wynd.controller.api.serve", lambda ctl, **kw: calls.append((ctl, kw)))
    return calls


@pytest.fixture
def plain_ctl(workspace, make_controller, use_controller):
    return use_controller(make_controller(workspace, env={"WYND_API_TOKEN": None}))


def test_serve_api_serves_the_workspace_controller_with_the_defaults(plain_ctl, cli, served):
    result = cli("serve-api")
    assert served == [(plain_ctl, DEFAULTS)]
    assert f"wynd serve-api: http://127.0.0.1:8780 (workspace {plain_ctl.ctx.root})" in result.stderr
    assert "warning" not in result.stderr and result.stdout == ""


def test_serve_api_passes_every_option(plain_ctl, cli, served, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    cli("serve-api", "--host", "0.0.0.0", "--port", "9000", "--web-dist", dist, "--no-scheduler", "--token", "t0k",
        "--cors-origin", "http://a.test", "--cors-origin", "http://b.test", "--log-level", "debug")
    assert served == [(plain_ctl, {"host": "0.0.0.0", "port": 9000, "web_dist": dist.resolve(), "api_token": "t0k",
                                   "cors_origins": ["http://a.test", "http://b.test"], "scheduler": False,
                                   "log_level": "debug"})]


def test_the_token_falls_back_to_the_controllers_env(workspace, make_controller, use_controller, cli, served):
    use_controller(make_controller(workspace, env={"WYND_API_TOKEN": "env-tok"}))
    cli("serve-api")
    cli("serve-api", "--token", "cli-tok")
    assert [kw["api_token"] for _, kw in served] == ["env-tok", "cli-tok"]


def test_the_token_can_come_from_the_workspace_env_file(plain_ctl, cli, served, workspace):
    (workspace / ".env").write_text("WYND_API_TOKEN=dotenv-tok\n")
    cli("serve-api")
    assert served[0][1]["api_token"] == "dotenv-tok"


def test_a_public_host_without_a_token_warns_but_still_serves(plain_ctl, cli, served):
    result = cli("serve-api", "--host", "0.0.0.0")
    assert "warning: serving on 0.0.0.0 without an API token" in result.stderr
    assert served[0][1]["host"] == "0.0.0.0" and served[0][1]["api_token"] is None
    assert "warning" not in cli("serve-api", "--host", "0.0.0.0", "--token", "t").stderr


@pytest.mark.parametrize("host", ["localhost", "127.0.0.2", "::1"])
def test_loopback_hosts_need_no_token(plain_ctl, cli, served, host):
    result = cli("serve-api", "--host", host)
    assert "warning" not in result.stderr
    assert ("http://[::1]:8780" if host == "::1" else f"http://{host}:8780") in result.stderr


def test_a_missing_web_dist_is_a_usage_error(plain_ctl, cli, served, tmp_path):
    cli("serve-api", "--web-dist", tmp_path / "missing", code=2)
    assert served == []


def test_serve_api_needs_a_workspace(cli, served, tmp_path):
    result = cli("-C", tmp_path, "serve-api", code=3)
    assert "no wynd.yaml found" in result.stderr and served == []


# --- search -----------------------------------------------------------------------------------------------------------

def rows(stdout: str) -> list[list[str]]:
    """Table rows below the header, split into the PROCESS and FLAGS columns and the rest (the match)."""
    lines = stdout.splitlines()
    assert lines[0].split() == ["PROCESS", "FLAGS", "MATCH"]
    return [line.split(None, 2) for line in lines[1:]]


def test_search_prints_each_hit_with_its_flags_and_first_match(plain_ctl, cli):
    assert rows(cli("search", "words").stdout) == [["p1", "-", "goal: Upper-case a text, count its words and save it."]]


def test_hits_are_sorted_by_score_then_id(plain_ctl, cli):
    found = rows(cli("search", "a").stdout)
    assert [row[0] for row in found] == ["parent", "p1", "p2", "p3"]       # parent matches on its id (weight 3)
    assert found[0][2] == "id: parent" and found[2][1] == "design"


def test_step_instructions_are_searched_and_every_word_must_match(plain_ctl, cli):
    expected = [["p1", "-", "goal: Upper-case a text, count its words and save it."]]
    assert rows(cli("search", "text", "blank").stdout) == expected
    assert rows(cli("search", "blank").stdout) == [
        ["p1", "-", "instruction upper: Upper-case the text; blank text takes the empty exit."]]
    assert rows(cli("search", "trim").stdout) == [
        ["p2", "design", "instruction normalise: Trim and lower-case the name."]]


def test_no_hit_prints_a_note_and_exits_0(plain_ctl, cli):
    result = cli("search", "text", "document")
    assert result.stdout == "" and "no process matches 'text document'" in result.stderr
    result = cli("search", "p", "--flag", "design", "--flag", "built")
    assert result.stdout == "" and "with design, built" in result.stderr


def test_flags_filter_the_hits(plain_ctl, cli):
    assert [row[:2] for row in rows(cli("search", "p", "--flag", "design").stdout)] == [["p2", "design"],
                                                                                         ["p3", "design"]]


def test_an_unknown_flag_is_a_usage_error(plain_ctl, cli):
    assert "unknown status flag 'shipped'" in cli("search", "p", "--flag", "shipped", code=2).stderr


def test_search_json_is_one_document_of_process_summaries(plain_ctl, cli):
    data = json.loads(cli("search", "blank", "--json").stdout)
    [hit] = data["items"]
    assert hit["id"] == "p1" and hit["status"]["design"] is False
    assert hit["matches"] == [{"field": "instruction", "step": "upper",
                               "snippet": "Upper-case the text; blank text takes the empty exit."}]


# --- release ----------------------------------------------------------------------------------------------------------

MANIFEST = EnvManifest(process="p1", vars=[
    EnvVar(name="RECORDS_DIR"),
    EnvVar(name="SERVICE_TOKEN", secret=True),
    EnvVar(name="WYND_RUN_API_TOKEN", secret=True, required=False, used_by=["runtime"]),
])
ENV = {"SVC_TOKEN": "svc-1", "HOOK_SECRET": "s3cr3t", "WYND_RUN_API_TOKEN": "run-tok", "WYND_API_TOKEN": None}
BIND = ("--env", "RECORDS_DIR=/data/records", "--env-from", "SERVICE_TOKEN=SVC_TOKEN")
CONTAINER_ENV = {"RECORDS_DIR": "/data/records", "SERVICE_TOKEN": "svc-1", "WYND_RUN_API_TOKEN": "run-tok"}
SCHEDULE = ("--trigger", "schedule", "--cron", "0 9 * * 1-5", "--tz", "Europe/London", "--input", "text=hello world")


@pytest.fixture
def api() -> FakeRunApi:
    return FakeRunApi()


@pytest.fixture
def ctl(workspace, make_controller, use_controller, api):
    ctl = make_controller(workspace, env=ENV)
    ctl.releases = ReleaseService(ctl.ctx, ctl, run_api_client=api)
    install_backends(ctl, FakeServing(api), FakeTriggers())
    return use_controller(ctl)


@pytest.fixture
def head(ctl) -> str:
    return process_head(ctl.ctx, "p1")[0]


@pytest.fixture
def built(ctl, head):
    return put_build(ctl, "p1", head, MANIFEST)


def details(stdout: str) -> list[tuple[str, str]]:
    """`print_release` lines as (name, value): names are padded to 10 characters (`next fire` has a space)."""
    return [(line[:10].strip(), line[11:]) for line in stdout.splitlines()]


def create(cli, *args: str) -> str:
    """`wynd release create p1 --commit HEAD <args> --json` -> the release id."""
    return json.loads(cli("release", "create", "p1", "--commit", "HEAD", *args, "--json").stdout)["id"]


def test_create_releases_the_build_at_the_process_head_and_starts_it(ctl, cli, commit, workspace, head, built):
    moved = commit(workspace, "outside p1's closure", {"README.md": "notes\n"})
    assert moved != head                                        # repo HEAD moved; p1's process HEAD did not

    result = cli("release", "create", "p1", "--commit", "HEAD", "--trigger", "manual", *BIND)

    [release] = ctl.releases.list()
    assert (release.commit, release.image, release.enabled, release.state) == (head, IMAGE, True, "serving")
    assert ctl.ctx.serving.ensured() == [(release.id, CONTAINER_ENV)]
    assert ctl.ctx.triggers.calls == [("install", release.id)]
    assert details(result.stdout) == [
        ("release", release.id), ("process", f"p1@{head[:7]}"), ("image", IMAGE), ("trigger", "manual"),
        ("enabled", "yes"), ("state", "serving"), ("env", "RECORDS_DIR=/data/records"),
        ("env", "SERVICE_TOKEN from $SVC_TOKEN"),
    ]
    assert "note:" not in result.stderr


def test_create_accepts_a_short_sha(ctl, cli, head, built):
    result = cli("release", "create", "p1", "--commit", head[:7], "--trigger", "manual", *BIND, "--json")
    assert json.loads(result.stdout)["commit"] == head


def test_create_a_schedule_with_fixed_inputs(ctl, cli, head, built):
    result = cli("release", "create", "p1", "--commit", "HEAD", *SCHEDULE, *BIND, "--json")

    data = json.loads(result.stdout)
    assert data["trigger"] == {"kind": "schedule", "cron": "0 9 * * 1-5", "timezone": "Europe/London",
                               "inputs": {"text": "hello world"}}
    assert data["next_fire_at"] is not None
    assert ("note: schedules fire while 'wynd serve-api' is running (trigger backend: fake-triggers)"
            in result.stderr)
    shown = dict(details(cli("release", "show", data["id"]).stdout))
    assert shown["trigger"] == "schedule 0 9 * * 1-5 (Europe/London)"
    assert shown["inputs"] == '{"text": "hello world"}'
    assert "T09:00+0" in shown["next fire"]                     # 09:00 London wall time (BST or GMT)


def test_a_schedule_needs_the_process_inputs(ctl, cli, head, built):
    result = cli("release", "create", "p1", "--commit", "HEAD", "--trigger", "schedule", "--cron", "@daily", *BIND,
                 code=2)
    assert "schedule inputs must be the inputs of process 'p1'" in result.stderr
    assert ctl.releases.list() == []


def test_create_a_webhook(ctl, cli, head, built):
    result = cli("release", "create", "p1", "--commit", "HEAD", "--trigger", "webhook", "--secret-env",
                 "HOOK_SECRET", *BIND)
    shown = dict(details(result.stdout))
    [release] = ctl.releases.list()
    assert shown["trigger"] == "webhook (secret HOOK_SECRET)"
    assert shown["webhook"] == (f"POST /hooks/releases/{release.id} on the serve-api address "
                                "(Authorization: Bearer $HOOK_SECRET)")
    assert "note: webhooks are received while 'wynd serve-api' is running" in result.stderr


def test_create_disabled_starts_nothing(ctl, cli, head, built):
    rid = create(cli, "--trigger", "manual", "--disabled", *BIND)
    release = ctl.releases.get(rid)
    assert (release.enabled, release.state) == (False, "stopped")
    assert ctl.ctx.serving.ensured() == [] and ctl.ctx.triggers.calls == []


@pytest.mark.parametrize(("args", "message"), [
    (("--trigger", "manual", "--cron", "0 9 * * *"), "--cron applies to schedule triggers only"),
    (("--trigger", "webhook", "--tz", "UTC"), "--tz applies to schedule triggers only"),
    (("--trigger", "manual", "--secret-env", "X"), "--secret-env applies to webhook triggers only"),
    (("--trigger", "webhook", "--input", "text=a"), "--input/--inputs/--inputs-file applies to schedule triggers"),
    (("--trigger", "schedule"), "a schedule trigger needs --cron EXPR"),
    (("--trigger", "hourly"), "--trigger must be one of manual, schedule, webhook"),
    (("--trigger", "manual", "--env", "RECORDS_DIR"), "--env expects VAR=VALUE"),
    (("--trigger", "manual", "--env", "A=1", "--env-from", "A=B"), "env var A is bound twice"),
    (("--trigger", "schedule", "--cron", "61 * * * *", "--input", "text=a"), "minute"),
])
def test_create_usage_errors_exit_2(ctl, cli, head, built, args, message):
    result = cli("release", "create", "p1", "--commit", "HEAD", *args, code=2)
    assert message in result.stderr
    assert ctl.releases.list() == []


def test_create_preconditions_exit_3(ctl, cli, head, built):
    result = cli("release", "create", "p2", "--commit", "HEAD", "--trigger", "manual", code=3)
    assert "process 'p2' has no image build" in result.stderr and "hint: run `wynd build p2`" in result.stderr
    assert "unknown process 'nope'" in cli("release", "create", "nope", "--commit", "HEAD", "--trigger", "manual",
                                           code=3).stderr
    unbound = cli("release", "create", "p1", "--commit", "HEAD", "--trigger", "manual", code=3)
    assert "required env var(s) with no binding: RECORDS_DIR, SERVICE_TOKEN" in unbound.stderr
    assert ctl.releases.list() == []


def test_list_and_show(ctl, cli, head, built):
    manual = create(cli, "--trigger", "manual", *BIND)
    hook = create(cli, "--trigger", "webhook", *BIND)

    lines = cli("release", "list").stdout.splitlines()
    assert lines[0].split() == ["RELEASE", "PROCESS", "COMMIT", "BEHIND", "TRIGGER", "ENABLED", "STATE", "NEXT", "FIRE"]
    listed = {line.split()[0]: line.split()[1:] for line in lines[1:]}
    assert listed == {manual: ["p1", head[:7], "0", "manual", "yes", "serving", "-"],
                      hook: ["p1", head[:7], "0", "webhook", "yes", "serving", "-"]}
    assert len(cli("release", "list", "p2").stdout.splitlines()) == 1
    assert {item["id"] for item in json.loads(cli("release", "list", "p1", "--json").stdout)["items"]} == {manual, hook}

    shown = dict(details(cli("release", "show", hook).stdout))
    assert shown["release"] == hook and shown["trigger"] == "webhook"
    assert shown["webhook"].endswith("(Authorization: Bearer $WYND_API_TOKEN)")
    assert json.loads(cli("release", "show", manual, "--json").stdout)["id"] == manual
    assert "unknown release 'rel_nope'" in cli("release", "show", "rel_nope", code=3).stderr


def test_a_release_makes_the_process_released_for_search(ctl, cli, head, built):
    create(cli, "--trigger", "manual", *BIND)
    assert [row[:2] for row in rows(cli("search", "p", "--flag", "released").stdout)] == [["p1", "built,released"]]


def test_run_fires_the_release_and_waits_for_the_mirrored_run(ctl, cli, api, head, built):
    rid = create(cli, "--trigger", "manual", *BIND)

    lines = cli("release", "run", rid, "--input", "text=hi there").stdout.splitlines()

    run_id = lines[0].removeprefix("run: ")
    assert lines[1] == "exit: done" and json.loads("\n".join(lines[2:])) == {"words": 2}
    [submit] = api.submits
    assert (submit["run_id"], submit["inputs"], submit["token"]) == (run_id, {"text": "hi there"}, "run-tok")
    assert submit["metadata"] == {"trigger": "manual", "release_id": rid, "target": {"kind": "release",
                                                                                     "commit": head}}
    run = ctl.runs.get(run_id)
    assert (run.status, run.trigger, run.release_id, run.target.kind) == ("succeeded", "manual", rid, "release")
    assert [event["type"] for event in ctl.runs.events(run_id)][-1] == "run.end"
    [fire] = ctl.releases.fires(rid)
    assert (fire.ok, fire.run_id, fire.source) == (True, run_id, "manual") and fire.finished_at is not None


def test_run_follow_prints_each_step(ctl, cli, head, built):
    rid = create(cli, "--trigger", "manual", *BIND)
    lines = cli("release", "run", rid, "--input", "text=hi", "--follow").stdout.splitlines()
    assert lines[0].startswith("run: run_")
    assert lines[1].split()[:2] == ["read", "done"]
    assert lines[2] == "exit: done"


def test_run_without_inputs_uses_the_schedules_inputs(ctl, cli, api, head, built):
    rid = create(cli, *SCHEDULE, *BIND)
    data = json.loads(cli("release", "run", rid, "--json").stdout)
    assert data["status"] == "succeeded" and data["release_id"] == rid
    assert api.submits[0]["inputs"] == {"text": "hello world"}


def test_a_run_ending_in_error_exits_1(ctl, cli, api, head, built):
    rid = create(cli, "--trigger", "manual", *BIND)
    api.exit = "error"
    result = cli("release", "run", rid, "--input", "text=hi", code=1)
    assert "exit: error" in result.stdout.splitlines()


def test_a_disabled_release_cannot_run(ctl, cli, api, head, built):
    rid = create(cli, "--trigger", "manual", "--disabled", *BIND)
    assert f"release '{rid}' is disabled" in cli("release", "run", rid, code=3).stderr
    assert api.submits == []
    [fire] = ctl.releases.fires(rid)
    assert fire.ok is False


def test_disable_then_enable(ctl, cli, head, built):
    rid = create(cli, "--trigger", "manual", *BIND)

    assert cli("release", "disable", rid).stdout.strip() == f"{rid}: disabled (stopped)"
    assert ctl.ctx.serving.removed() == [rid] and ctl.releases.get(rid).enabled is False

    assert cli("release", "enable", rid).stdout.strip() == f"{rid}: enabled (serving)"
    assert [release_id for release_id, _ in ctl.ctx.serving.ensured()] == [rid, rid]
    assert json.loads(cli("release", "disable", rid, "--json").stdout)["enabled"] is False


def test_delete_keeps_the_fire_history(ctl, cli, head, built):
    rid = create(cli, "--trigger", "manual", *BIND)
    run_id = cli("release", "run", rid, "--input", "text=hi").stdout.splitlines()[0].removeprefix("run: ")

    assert cli("release", "delete", rid).stdout.strip() == f"deleted {rid}"
    assert ctl.ctx.serving.removed() == [rid] and ("remove", rid) in ctl.ctx.triggers.calls
    cli("release", "show", rid, code=3)

    lines = cli("release", "fires", rid).stdout.splitlines()
    assert lines[0].split() == ["FIRE", "SOURCE", "AT", "RUN", "OK", "ERROR"]
    [row] = [line.split() for line in lines[1:]]
    assert (row[1], row[3], row[4], row[5]) == ("manual", run_id, "yes", "-")
    assert json.loads(cli("release", "fires", rid, "--json").stdout)["items"][0]["run_id"] == run_id

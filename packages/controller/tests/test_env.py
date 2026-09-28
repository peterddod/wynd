"""`.env` files and `EnvService` (PLAN §8.1 envfile/envcheck rows, §3.10; `$DRAFTS/06 §5.11`)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest

from wynd.controller.api.models_web import FromEnvBinding, ManualTrigger, Release, ValueBinding
from wynd.controller.envfile import load_into_environ, parse_env_file, read_env_file
from wynd.controller.errors import Invalid
from wynd.process.artefacts import BuildInfo
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.fragments import EnvGroup, EnvVar

P1_YAML = "processes/p1/process.yaml"


def build_manifest(pid: str = "p1") -> EnvManifest:
    return EnvManifest(process=pid, vars=[
        EnvVar(name="WYND_T_API_KEY", secret=True, one_of="auth", used_by=["provider:x"]),
        EnvVar(name="OPTIONAL_DIR", required=False),
        EnvVar(name="RECORDS_DIR", description="records"),
        EnvVar(name="WYND_T_TOKEN", secret=True, one_of="auth"),
        EnvVar(name="WITH_DEFAULT", default="x"),
    ], groups={"auth": EnvGroup(description="One credential.", modes=["image"])})


def put_build(controller, commit: str, manifest: EnvManifest) -> None:
    staged = controller.ctx.state_dir / "tmp" / "stage"
    staged.mkdir(parents=True)
    controller.ctx.artefacts.put_build(staged, BuildInfo(
        process=manifest.process, commit=commit, source_sha=commit, job_id=None, process_hash="sha256:" + "0" * 64,
        image="img", image_id=None, image_digest=None, base={}, manifest=manifest,
        created_at=datetime(2026, 9, 22, tzinfo=UTC), dir=""))


# --- .env files ---------------------------------------------------------------------------------------------------

def test_parse_env_file():
    text = """
# a comment
export EXPORTED=1
PLAIN = value with spaces   # trailing comment
URL=http://host/x#fragment
EMPTY=
SINGLE='literal \\n # not a comment'
DOUBLE="line1\\nline2 \\"quoted\\" back\\\\slash \\t tab"   # comment
HASH_IN_DOUBLE="a # b"
"""
    assert parse_env_file(text) == {
        "EXPORTED": "1",
        "PLAIN": "value with spaces",
        "URL": "http://host/x#fragment",
        "EMPTY": "",
        "SINGLE": "literal \\n # not a comment",
        "DOUBLE": 'line1\nline2 "quoted" back\\slash \t tab',
        "HASH_IN_DOUBLE": "a # b",
    }


@pytest.mark.parametrize(("text", "values"), [
    ("A=1\nB=two words", {"A": "1", "B": "two words"}),
    ("export A='x y'", {"A": "x y"}),
    ("A='a#b' # c", {"A": "a#b"}),
    ('A="x\\ny"', {"A": "x\ny"}),
    ('A="x\\qy"', {"A": "x\\qy"}),                  # unknown escapes are kept
    ("A=${B}", {"A": "${B}"}),                     # no interpolation
    ("\n\n  # only comments\n", {}),
])
def test_parse_env_file_values(text, values):
    assert parse_env_file(text) == values


@pytest.mark.parametrize(("text", "error"), [
    ("NOT A LINE", ".env:1: expected KEY=VALUE"),
    ("A=1\n1BAD=2", ".env:2: expected KEY=VALUE"),
    ("A='open", ".env:1: unterminated single quote"),
    ('A="open', ".env:1: unterminated double quote"),
    ("A='x' y", ".env:1: unexpected text after the closing quote"),
])
def test_parse_env_file_errors(text, error):
    with pytest.raises(Invalid, match=error):
        parse_env_file(text)


def test_read_and_load_env_file(tmp_path, monkeypatch):
    assert read_env_file(tmp_path / ".env") == {}
    (tmp_path / ".env").write_text("WYND_T_NEW=from-file\nWYND_T_SET=from-file\n")
    for name in ("WYND_T_NEW", "WYND_T_SET"):
        monkeypatch.setenv(name, "x")                                  # so monkeypatch restores both afterwards
    monkeypatch.delenv("WYND_T_NEW")
    load_into_environ(tmp_path)
    assert (os.environ["WYND_T_NEW"], os.environ["WYND_T_SET"]) == ("from-file", "x")


# --- resolve --------------------------------------------------------------------------------------------------------

def test_resolve_precedence(workspace, make_controller, tmp_path):
    (workspace / ".env").write_text("A=dotenv\nB=dotenv\nC=dotenv\n")
    first, second = tmp_path / "first.env", tmp_path / "second.env"
    first.write_text("A=first\nB=first\n")
    second.write_text("A=second\nB=second\nD=second\n")
    controller = make_controller(workspace, env={"A": "environ"})
    controller.ctx.stores.registry.put_secret("C", "secret")
    controller.ctx.stores.registry.put_secret("E", "secret")
    values = controller.env.resolve([first, second])
    assert {k: values[k] for k in "ABCDE"} == {"A": "environ", "B": "first", "C": "dotenv", "D": "second",
                                               "E": "secret"}
    assert values["PATH"] == os.environ["PATH"]                        # the whole environment is passed on


def test_resolve_refreshes_oauth_tokens_first(controller, monkeypatch):
    import wynd.controller.oauth as oauth
    from wynd.runtime.mcp.entry import McpServerEntry

    def refresh(registry):
        registry.put_secret("WYND_MCP_LINEAR_TOKEN", "fresh")
        return ["linear"]

    monkeypatch.setattr(oauth, "refresh_tokens", refresh)
    controller.ctx.stores.registry.put_secret("WYND_MCP_LINEAR_TOKEN", "stale")
    assert controller.env.resolve()["WYND_MCP_LINEAR_TOKEN"] == "stale"          # no OAuth entry: nothing refreshed
    controller.registries.add_mcp(McpServerEntry(
        name="linear", transport="http", url="https://mcp.linear.app/mcp", oauth={"client_id": "c"},
        headers={"Authorization": "Bearer ${env:WYND_MCP_LINEAR_TOKEN}"}))
    assert controller.env.resolve()["WYND_MCP_LINEAR_TOKEN"] == "fresh"


def test_resolve_image_adds_the_registry_snapshot_only_when_used(workspace, make_controller, commit, agentic_files):
    commit(workspace, "p4", agentic_files("p4"))
    controller = make_controller(workspace)
    controller.ctx.stores.registry.put("providers", "fake", {"tiers": {"cheap": "fake-small"}})
    assert "WYND_REGISTRY_JSON" not in controller.env.resolve_image("p1")      # no agentic step
    snapshot = controller.env.resolve_image("p4")["WYND_REGISTRY_JSON"]
    assert snapshot == '{"providers":{"fake":{"tiers":{"cheap":"fake-small"}}}}'
    assert json.loads(snapshot) == {"providers": {"fake": {"tiers": {"cheap": "fake-small"}}}}


# --- manifest and check ---------------------------------------------------------------------------------------------

def test_manifest_is_assembled_until_built(controller, workspace, git, commit):
    first = git(workspace, "rev-parse", "HEAD").strip()
    assembled = controller.env.manifest("p1")
    records = next(v for v in assembled.vars if v.name == "RECORDS_DIR")
    assert (records.required, records.description) == (True, "Directory where the upper-cased text is saved.")
    assert records.used_by == ["edge:p1:upper.done[0].dest"]

    commit(workspace, "rename var", {P1_YAML: (workspace / P1_YAML).read_text().replace("RECORDS_DIR", "OUT_DIR")})
    names = {v.name for v in controller.env.manifest("p1").vars}
    assert "OUT_DIR" in names and "RECORDS_DIR" not in names
    assert "RECORDS_DIR" in {v.name for v in controller.env.manifest("p1", first).vars}   # assembled at the commit

    put_build(controller, git(workspace, "rev-parse", "HEAD").strip(), build_manifest())
    assert controller.env.manifest("p1") == build_manifest()


def test_check_local_names_missing_vars_never_values(workspace, make_controller):
    controller = make_controller(workspace, env={"RECORDS_DIR": ""})    # empty counts as unset
    check = controller.env.check("p1")
    assert (check.ok, check.missing, check.unbound) == (False, ["RECORDS_DIR"], [])
    assert [(i.severity, i.code) for i in check.issues] == [("error", "E-ENV-MISSING")]
    assert "RECORDS_DIR" in check.issues[0].message

    (workspace / ".env").write_text("RECORDS_DIR=/tmp/records-secret-path\n")
    assert not controller.env.check("p1").ok                          # the environ's empty value wins over .env
    check = make_controller(workspace, env={"RECORDS_DIR": None}).env.check("p1")
    assert check.model_dump() == {"ok": True, "missing": [], "unbound": [], "issues": []}


def test_check_accepts_a_registry_secret_and_extra_files(workspace, make_controller, tmp_path):
    controller = make_controller(workspace, env={"RECORDS_DIR": None})
    extra = tmp_path / "extra.env"
    extra.write_text("RECORDS_DIR=/r\n")
    assert controller.env.check("p1", extra_files=[extra]).ok
    assert not controller.env.check("p1").ok
    controller.ctx.stores.registry.put_secret("RECORDS_DIR", "/r")
    assert controller.env.check("p1").ok


def test_check_groups_by_mode_against_the_build_manifest(workspace, make_controller, git):
    controller = make_controller(workspace, env={"RECORDS_DIR": None})
    put_build(controller, git(workspace, "rev-parse", "HEAD").strip(), build_manifest())
    local = controller.env.check("p1", mode="local")
    assert (local.ok, local.missing, local.unbound) == (False, ["RECORDS_DIR"], ["WYND_T_API_KEY", "WYND_T_TOKEN"])
    assert sorted(i.code for i in local.issues) == ["E-ENV-MISSING", "W-ENV-ONE-OF"]
    image = controller.env.check("p1", mode="image")
    assert (image.ok, image.missing, image.unbound) == (False, ["RECORDS_DIR", "WYND_T_API_KEY", "WYND_T_TOKEN"], [])


def test_check_release_reports_unbound_and_missing(workspace, make_controller, git):
    controller = make_controller(workspace, env={"RECORDS_DIR": None})
    commit = git(workspace, "rev-parse", "HEAD").strip()
    put_build(controller, commit, build_manifest())

    def release(env) -> Release:
        return Release(id="rel_1", process_id="p1", commit=commit, short=commit[:7], image="img",
                       trigger=ManualTrigger(), env=env, enabled=True, state="stopped",
                       created_at=datetime(2026, 9, 22, tzinfo=UTC))

    check = controller.env.check_release(release({}))
    assert (check.ok, check.unbound, check.missing) == (False, ["RECORDS_DIR", "WYND_T_API_KEY", "WYND_T_TOKEN"], [])
    check = controller.env.check_release(release({"RECORDS_DIR": ValueBinding(value="/data"),
                                                  "WYND_T_TOKEN": FromEnvBinding(from_env="WYND_T_UNSET_TOKEN")}))
    assert (check.ok, check.unbound, check.missing) == (False, [], ["WYND_T_TOKEN"])
    (workspace / ".env").write_text("WYND_T_UNSET_TOKEN=abc\n")
    assert controller.env.check_release(release({"RECORDS_DIR": ValueBinding(value="/data"),
                                                  "WYND_T_TOKEN": FromEnvBinding(from_env="WYND_T_UNSET_TOKEN")})).ok

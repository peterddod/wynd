"""`wynd env check` (PLAN §9, §3.22, §3.10; `$DRAFTS/06 §9.2`, §10.4) over a real controller: `p1` needs
`RECORDS_DIR` (declared in `process.env.vars`, used by an edge); exit 1 while it is missing. The table reports where
each value would come from without ever printing a value."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from wynd.process.artefacts import BuildInfo
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.fragments import EnvGroup, EnvVar

SECRET = "s3cr3t-value"


def row(stdout: str, name: str) -> list[str]:
    return next(line.split()[:4] for line in stdout.splitlines() if line.startswith(f"{name} "))


def test_missing_required_var_exits_1(workspace, make_controller, use_controller, cli):
    use_controller(make_controller(workspace, env={"RECORDS_DIR": None}))
    result = cli("env", "check", "p1", code=1)
    lines = result.stdout.splitlines()
    assert lines[0].split() == ["VAR", "REQUIRED", "SET", "SOURCE", "USED", "BY", "DESCRIPTION"]
    records = next(line for line in lines if line.startswith("RECORDS_DIR "))
    assert records.split()[:5] == ["RECORDS_DIR", "yes", "no", "missing", "edge:p1:upper.done[0].dest"]
    assert records.endswith("Directory where the upper-cased text is saved.")
    assert "manifest: assembled from step locks" in lines
    assert any(line.startswith("ERROR  E-ENV-MISSING  ") and "RECORDS_DIR" in line for line in lines)
    assert lines[-1] == "missing: RECORDS_DIR"
    assert row(result.stdout, "WYND_PORT") == ["WYND_PORT", "no", "no", "default"]
    assert row(result.stdout, "WYND_RUN_API_TOKEN") == ["WYND_RUN_API_TOKEN", "no", "no", "unset"]

    doc = json.loads(cli("env", "check", "p1", "--json", code=1).stdout)
    assert (doc["process"], doc["manifest"], doc["commit"], doc["ok"]) == ("p1", "assembled", None, False)
    var = next(v for v in doc["vars"] if v["name"] == "RECORDS_DIR")
    assert (var["required"], var["set"], var["source"], var["used_by"]) == (
        True, False, "missing", ["edge:p1:upper.done[0].dest"])


def test_sources_by_precedence(workspace, tmp_path, make_controller, use_controller, cli):
    ctl = use_controller(make_controller(workspace, env={"RECORDS_DIR": None}))
    ctl.ctx.stores.registry.put_secret("RECORDS_DIR", SECRET)
    result = cli("env", "check", "p1")
    assert row(result.stdout, "RECORDS_DIR") == ["RECORDS_DIR", "yes", "yes", "registry"]
    assert result.stdout.splitlines()[-1] == "ok" and SECRET not in result.stdout

    (workspace / ".env").write_text(f"RECORDS_DIR={SECRET}\n")
    assert row(cli("env", "check", "p1").stdout, "RECORDS_DIR")[3] == "dotenv"

    extra = tmp_path / "prod.env"
    extra.write_text(f"RECORDS_DIR={SECRET}-prod\n")
    result = cli("env", "check", "p1", "--env-file", extra)
    assert row(result.stdout, "RECORDS_DIR")[3] == "file" and SECRET not in result.stdout

    use_controller(make_controller(workspace, env={"RECORDS_DIR": f"{SECRET}-env"}))
    result = cli("env", "check", "p1", "--env-file", extra, "--json")
    var = next(v for v in json.loads(result.stdout)["vars"] if v["name"] == "RECORDS_DIR")
    assert (var["set"], var["source"]) == (True, "env") and SECRET not in result.stdout


def test_a_dotenv_value_loaded_into_the_environment_counts_as_dotenv(workspace, make_controller, use_controller,
                                                                     cli):
    (workspace / ".env").write_text("RECORDS_DIR=/data/records\n")
    use_controller(make_controller(workspace, env={"RECORDS_DIR": "/data/records"}))   # as Controller.open loads it
    assert row(cli("env", "check", "p1").stdout, "RECORDS_DIR")[3] == "dotenv"


def test_the_build_manifest_at_the_process_head(workspace, make_controller, use_controller, cli, git):
    ctl = use_controller(make_controller(workspace, env={"RECORDS_DIR": None}))
    head = git(workspace, "rev-parse", "HEAD").strip()
    manifest = EnvManifest(process="p1", commit=head, vars=[
        EnvVar(name="RECORDS_DIR", description="records"),
        EnvVar(name="WYND_T_TOKEN", secret=True, required=False, one_of="auth", used_by=["provider:x"]),
    ], groups={"auth": EnvGroup(modes=["image"])})
    staged = ctl.ctx.state_dir / "tmp" / "stage"
    staged.mkdir(parents=True)
    ctl.ctx.artefacts.put_build(staged, BuildInfo(
        process="p1", commit=head, source_sha=head, job_id=None, process_hash="sha256:" + "0" * 64, image="img",
        image_id=None, image_digest=None, base={}, manifest=manifest, created_at=datetime(2026, 9, 22, tzinfo=UTC),
        dir=""))
    result = cli("env", "check", "p1", code=1)
    lines = result.stdout.splitlines()
    assert f"manifest: build @{head[:7]}" in lines
    assert row(result.stdout, "WYND_T_TOKEN")[:4] == ["WYND_T_TOKEN", "one", "of", "auth"]
    assert any(line.startswith("WARN   W-ENV-ONE-OF  ") for line in lines)              # image-only group, local check
    doc = json.loads(cli("env", "check", "p1", "--json", code=1).stdout)
    assert (doc["manifest"], doc["commit"]) == ("build", head)
    assert [v["name"] for v in doc["vars"]] == ["RECORDS_DIR", "WYND_T_TOKEN"]


def test_unknown_process_exits_3(workspace, make_controller, use_controller, cli):
    use_controller(make_controller(workspace))
    assert "unknown process" in cli("env", "check", "nope", code=3).stderr

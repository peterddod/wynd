"""Env fragments, merging and the env check ($DRAFTS/01 §6.2, §12.10; PLAN §3.10)."""

import pytest

from wynd.spec import (
    EnvCheck,
    EnvFragment,
    EnvGroup,
    EnvManifest,
    EnvVar,
    SpecError,
    check_env,
    dump_lock,
    load_env_manifest,
    merge_env_vars,
    merge_fragments,
)

CLAUDE_AUTH = [
    EnvVar(name="CLAUDE_CODE_OAUTH_TOKEN", secret=True, required=False, one_of="claude-code-auth",
           used_by=["provider:claude-code"]),
    EnvVar(name="ANTHROPIC_API_KEY", secret=True, required=False, one_of="claude-code-auth",
           used_by=["provider:claude-code"]),
]
GROUPS = {"claude-code-auth": EnvGroup(description="Claude Code auth", modes=["image"])}


def test_merge_env_vars_rules():
    merged = merge_env_vars([
        EnvVar(name="RECORDS_DIR", used_by=["edge:p:validate.done[0].dest"]),
        EnvVar(name="A_KEY", secret=True, required=False, used_by=["tool:p#x/t"]),
        EnvVar(name="RECORDS_DIR", description="Where records go", required=False, default="/tmp/r",
               one_of="dirs", used_by=["step:p#save", "edge:p:validate.done[0].dest"]),
        EnvVar(name="A_KEY", description="first wins", required=True, default="k", one_of="later"),
        EnvVar(name="A_KEY", description="ignored", default="k"),
    ])
    assert [v.model_dump() for v in merged] == [
        {"name": "A_KEY", "description": "first wins", "secret": True, "required": True, "default": "k",
         "one_of": "later", "used_by": ["tool:p#x/t"]},
        {"name": "RECORDS_DIR", "description": "Where records go", "secret": False, "required": True,
         "default": "/tmp/r", "one_of": "dirs", "used_by": ["edge:p:validate.done[0].dest", "step:p#save"]},
    ]


def test_merge_env_vars_conflicting_defaults():
    with pytest.raises(SpecError) as err:
        merge_env_vars([EnvVar(name="X", default="a", used_by=["step:p#a"]),
                        EnvVar(name="X", default="b", used_by=["step:p#b"])])
    [diag] = err.value.diagnostics
    assert diag.code == "E-ENV-CONFLICT"
    assert diag.message == "env var X has conflicting defaults 'a' and 'b' (used by step:p#a, step:p#b)"


def test_merge_fragments():
    merged = merge_fragments([
        EnvFragment(deps=["pypdf>=6,<7", "requests"], system=["poppler-utils"], vars=[EnvVar(name="B")],
                    groups={"auth": EnvGroup(description="first", modes=["image"])}),
        EnvFragment(deps=["requests", "claude-agent-sdk>=0.2.157,<0.3"], system=["curl", "poppler-utils"],
                    requires="glibc", vars=[EnvVar(name="A")], groups={"auth": EnvGroup(modes=["local"])}),
        EnvFragment(),
    ])
    assert merged.deps == ["pypdf>=6,<7", "requests", "claude-agent-sdk>=0.2.157,<0.3"]
    assert merged.system == ["curl", "poppler-utils"]
    assert merged.requires == "glibc"
    assert [v.name for v in merged.vars] == ["A", "B"]
    assert merged.groups == {"auth": EnvGroup(description="first", modes=["local", "image"])}
    assert merge_fragments([]) == EnvFragment()


def manifest(*vars, groups=None) -> EnvManifest:
    return EnvManifest(process="p", vars=sorted(vars, key=lambda v: v.name), groups=groups or {})


def test_check_env_required_missing_and_satisfied():
    m = manifest(EnvVar(name="RECORDS_DIR", used_by=["edge:p:validate.done[0].dest"]),
                 EnvVar(name="WYND_HOST", required=True, default="0.0.0.0", used_by=["runtime"]),
                 EnvVar(name="OPTIONAL", required=False))
    result = check_env(m, {"RECORDS_DIR": ""}, mode="local")
    assert result == EnvCheck(ok=False, diagnostics=result.diagnostics)
    assert [(d.severity, d.code, d.message) for d in result.diagnostics] == [
        ("error", "E-ENV-MISSING", "required env var RECORDS_DIR is not set (used by edge:p:validate.done[0].dest)"),
    ]
    assert check_env(m, {"RECORDS_DIR": "/r"}, mode="local") == EnvCheck(ok=True, diagnostics=[])


def test_check_env_one_of_groups_follow_modes():
    m = manifest(*CLAUDE_AUTH, groups=GROUPS)
    local = check_env(m, {}, mode="local")
    assert local.ok
    assert [(d.severity, d.code) for d in local.diagnostics] == [("warning", "W-ENV-ONE-OF")]
    assert "ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN" in local.diagnostics[0].message
    image = check_env(m, {"ANTHROPIC_API_KEY": ""}, mode="image")
    assert not image.ok
    assert [(d.code, d.message) for d in image.diagnostics] == [
        ("E-ENV-ONE-OF", "set one of ANTHROPIC_API_KEY, CLAUDE_CODE_OAUTH_TOKEN (group claude-code-auth; Claude Code "
                         "auth) (used by provider:claude-code)"),
    ]
    assert check_env(m, {"CLAUDE_CODE_OAUTH_TOKEN": "tok"}, mode="image") == EnvCheck(ok=True, diagnostics=[])


def test_check_env_group_without_declaration_is_required_everywhere():
    m = manifest(EnvVar(name="A", required=False, one_of="g"), EnvVar(name="B", required=False, one_of="g"))
    assert [d.code for d in check_env(m, {}, mode="local").diagnostics] == ["E-ENV-ONE-OF"]
    assert check_env(m, {"B": "1"}, mode="local").ok


def test_check_env_never_prints_values():
    m = manifest(EnvVar(name="SECRET", secret=True), *CLAUDE_AUTH, groups=GROUPS)
    environ = {"SECRET": "", "ANTHROPIC_API_KEY": "", "OTHER": "hunter2"}
    for mode in ("local", "image"):
        for diag in check_env(m, environ, mode=mode).diagnostics:
            assert "hunter2" not in diag.format()
    assert all("hunter2" not in d.format() for d in check_env(m, {"SECRET": "hunter2"}, mode="image").diagnostics)


def test_env_manifest_file_round_trip(tmp_path):
    m = manifest(*CLAUDE_AUTH, EnvVar(name="RECORDS_DIR", description="Records", used_by=["edge:p:x[0].dest"]),
                 groups=GROUPS)
    path = tmp_path / "process.env.yaml"
    path.write_text(dump_lock(m))
    assert load_env_manifest(path) == m
    assert "default" not in path.read_text()

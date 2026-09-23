"""Provider registry: entry points, metadata, tier resolution, the test seam and readiness checks (PLAN §3.15;
`$DRAFTS/03 §5.2–§5.3`, §16 `test_provider_registry.py`)."""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

import wynd.runtime.providers as providers
from wynd.runtime.errors import StepFailure
from wynd.runtime.providers import (
    DEFAULT_PROVIDER,
    ProviderEntry,
    available_providers,
    check_provider,
    load_provider,
    provider_info,
    provider_tiers,
    register_for_tests,
    resolve_model,
)
from wynd.runtime.providers import claude_code
from wynd.runtime.providers.anthropic import AnthropicProvider
from wynd.runtime.providers.claude_code import ClaudeCodeProvider
from wynd.runtime.providers.fake import FakeProvider
from wynd.runtime.providers.scripted import ScriptedAgentProvider, ScriptedModelProvider
from wynd.runtime.storage.local import FileRegistry
from wynd.spec.fragments import EnvFragment

CLAUDE_TIERS = {"cheap": "haiku", "standard": "sonnet", "strong": "opus"}
ANTHROPIC_TIERS = {"cheap": "claude-haiku-4-5", "standard": "claude-sonnet-5", "strong": "claude-opus-5"}


class Dummy:
    name = "dummy"
    kind = "model"
    default_tiers = {"cheap": "d-small", "standard": "d-medium"}   # no "strong" tier
    env_fragment = EnvFragment(deps=["dummy-sdk>=1"])

    def __init__(self, tiers):
        self.created_with = dict(tiers)


@pytest.fixture
def registry(tmp_path: Path) -> FileRegistry:
    return FileRegistry(tmp_path / "home")


@pytest.fixture
def dummy():
    undo = register_for_tests("dummy", Dummy)
    yield Dummy
    undo()


# --- entry points and metadata -------------------------------------------------------------------------------------


def test_default_provider_is_claude_code():
    assert DEFAULT_PROVIDER == "claude-code"
    assert providers.DEFAULT_PROVIDER == "claude-code"


def test_entry_points_resolve_the_shipped_providers():
    kinds = available_providers()
    assert {k: kinds[k] for k in ("claude-code", "anthropic", "fake")} == {
        "claude-code": "agent", "anthropic": "model", "fake": "agent"}
    assert providers._provider_class("claude-code") is ClaudeCodeProvider
    assert providers._provider_class("anthropic") is AnthropicProvider
    assert providers._provider_class("fake") is FakeProvider


def test_provider_info_claude_code():
    info = provider_info("claude-code")
    assert (info.name, info.kind, info.default_tiers) == ("claude-code", "agent", CLAUDE_TIERS)
    frag = info.env_fragment
    assert frag.deps == ["claude-agent-sdk>=0.2.157,<0.3"]
    assert frag.system == []
    assert frag.requires == "glibc"
    assert [(v.name, v.secret, v.required, v.one_of) for v in frag.vars] == [
        ("CLAUDE_CODE_OAUTH_TOKEN", True, False, "claude-code-auth"),
        ("ANTHROPIC_API_KEY", True, False, "claude-code-auth"),
    ]
    assert all(v.used_by == ["provider:claude-code"] for v in frag.vars)
    assert frag.groups["claude-code-auth"].modes == ["image"]


def test_provider_info_anthropic_and_fake():
    info = provider_info("anthropic")
    assert (info.kind, info.default_tiers) == ("model", ANTHROPIC_TIERS)
    assert info.env_fragment.deps == [] and info.env_fragment.requires is None
    assert [(v.name, v.secret, v.required, v.default) for v in info.env_fragment.vars] == [
        ("ANTHROPIC_API_KEY", True, True, None),
        ("ANTHROPIC_BASE_URL", False, False, "https://api.anthropic.com"),
    ]
    fake = provider_info("fake")
    assert (fake.kind, fake.default_tiers) == ("agent", {"cheap": "fake", "standard": "fake", "strong": "fake"})
    assert fake.env_fragment.deps == []
    assert [(v.name, v.required) for v in fake.env_fragment.vars] == [("WYND_FAKE_PROVIDER_SCRIPT", False)]


def test_provider_info_returns_a_copy_of_the_default_tiers():
    info = provider_info("claude-code")
    info.default_tiers["cheap"] = "changed"
    assert ClaudeCodeProvider.default_tiers["cheap"] == "haiku"


def test_unknown_provider_is_a_key_error():
    with pytest.raises(KeyError):
        provider_info("no-such-provider")
    with pytest.raises(KeyError):
        provider_tiers("no-such-provider", None)
    with pytest.raises(KeyError):
        load_provider("no-such-provider")


def test_metadata_needs_no_sdk_import():
    code = textwrap.dedent("""
        import sys
        from wynd.runtime.providers import available_providers, load_provider, provider_info, resolve_model
        available_providers()
        for name in ("claude-code", "anthropic", "fake"):
            provider_info(name)
            resolve_model(name, "cheap", None)
            load_provider(name)
        assert "claude_agent_sdk" not in sys.modules, "claude_agent_sdk was imported"
    """)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# --- tiers -----------------------------------------------------------------------------------------------------------


def test_provider_tiers_without_registry_are_the_defaults():
    assert provider_tiers("claude-code", None) == CLAUDE_TIERS
    assert provider_tiers("anthropic", None) == ANTHROPIC_TIERS


def test_provider_tiers_overlay_the_registry_entry(registry: FileRegistry):
    assert provider_tiers("claude-code", registry) == CLAUDE_TIERS          # no entry
    registry.put("providers", "claude-code", ProviderEntry(tiers={"cheap": "sonnet"}).model_dump())
    assert provider_tiers("claude-code", registry) == {**CLAUDE_TIERS, "cheap": "sonnet"}
    assert provider_tiers("anthropic", registry) == ANTHROPIC_TIERS          # other providers untouched


def test_provider_entry_rejects_unknown_keys_and_tiers(registry: FileRegistry):
    with pytest.raises(ValidationError):
        ProviderEntry.model_validate({"tiers": {"cheap": "x"}, "models": {}})
    registry.put("providers", "anthropic", {"tiers": {"turbo": "claude-x"}})
    with pytest.raises(ValidationError):
        provider_tiers("anthropic", registry)


def test_resolve_model_uses_defaults_and_registry(registry: FileRegistry):
    assert resolve_model("claude-code", "cheap", None) == "haiku"
    assert resolve_model("anthropic", "strong", None) == "claude-opus-5"
    assert resolve_model("fake", "standard", None) == "fake"
    registry.put("providers", "anthropic", {"tiers": {"strong": "claude-opus-5-5"}})
    assert resolve_model("anthropic", "strong", registry) == "claude-opus-5-5"
    assert resolve_model("anthropic", "cheap", registry) == "claude-haiku-4-5"


def test_resolve_model_unknown_provider_is_a_config_failure():
    with pytest.raises(StepFailure) as err:
        resolve_model("no-such-provider", "cheap", None)
    assert err.value.cause == "config"
    assert err.value.message == "provider 'no-such-provider' is not installed (entry point group wynd.providers)"


def test_resolve_model_missing_tier_is_a_config_failure(dummy):
    assert resolve_model("dummy", "cheap", None) == "d-small"
    with pytest.raises(StepFailure) as err:
        resolve_model("dummy", "strong", None)
    assert err.value.cause == "config"
    assert err.value.message == (
        "provider 'dummy' has no model for tier 'strong'; run `wynd provider add dummy --tier strong=<model>`")


def test_resolve_model_missing_tier_is_filled_by_the_registry(dummy, registry: FileRegistry):
    registry.put("providers", "dummy", {"tiers": {"strong": "d-large"}})
    assert resolve_model("dummy", "strong", registry) == "d-large"


def test_resolve_model_invalid_registry_entry_is_a_config_failure(registry: FileRegistry):
    registry.put("providers", "claude-code", {"tiers": {"cheap": "haiku"}, "model": "x"})
    with pytest.raises(StepFailure) as err:
        resolve_model("claude-code", "cheap", registry)
    assert err.value.cause == "config"
    assert err.value.message.startswith("user registry entry providers.claude-code is invalid:")


# --- loading and the test seam --------------------------------------------------------------------------------------


def test_load_provider_builds_the_class_with_effective_tiers(registry: FileRegistry):
    registry.put("providers", "claude-code", {"tiers": {"strong": "opus[1m]"}})
    p = load_provider("claude-code", registry=registry)
    assert isinstance(p, ClaudeCodeProvider)
    assert p.name == "claude-code"
    assert p.tiers() == {**CLAUDE_TIERS, "strong": "opus[1m]"}
    a = load_provider("anthropic")
    assert isinstance(a, AnthropicProvider) and a.tiers() == ANTHROPIC_TIERS
    f = load_provider("fake")
    assert isinstance(f, FakeProvider) and f.tiers() == {"cheap": "fake", "standard": "fake", "strong": "fake"}


def test_load_provider_returns_a_fresh_instance_per_call():
    assert load_provider("fake") is not load_provider("fake")


def test_register_for_tests_adds_and_undo_removes(dummy):
    assert available_providers()["dummy"] == "model"
    info = provider_info("dummy")
    assert (info.name, info.kind, info.env_fragment.deps) == ("dummy", "model", ["dummy-sdk>=1"])
    p = load_provider("dummy")
    assert isinstance(p, Dummy) and p.created_with == Dummy.default_tiers


def test_register_for_tests_undo_restores_the_previous_resolution():
    undo = register_for_tests("fake", Dummy)
    assert isinstance(load_provider("fake"), Dummy)
    inner_undo = register_for_tests("fake", ScriptedAgentProvider)
    assert provider_info("fake").kind == "agent"
    inner_undo()
    assert isinstance(load_provider("fake"), Dummy)
    undo()
    assert isinstance(load_provider("fake"), FakeProvider)
    undo_new = register_for_tests("brand-new", Dummy)
    undo_new()
    with pytest.raises(KeyError):
        provider_info("brand-new")
    assert "brand-new" not in available_providers()


def test_scripted_instances_stand_in_for_provider_classes(registry: FileRegistry):
    agent = ScriptedAgentProvider([])
    model = ScriptedModelProvider([], name="scripted-model")
    undo_a = register_for_tests("scripted", agent)
    undo_m = register_for_tests("scripted-model", model)
    try:
        registry.put("providers", "scripted", {"tiers": {"strong": "big"}})
        assert load_provider("scripted", registry=registry) is agent
        assert agent.tiers() == {"cheap": "scripted", "standard": "scripted", "strong": "big"}
        assert resolve_model("scripted", "strong", registry) == "big"
        assert provider_info("scripted").kind == "agent"
        assert load_provider("scripted-model") is model
        assert available_providers()["scripted-model"] == "model"
    finally:
        undo_a()
        undo_m()


# --- readiness --------------------------------------------------------------------------------------------------------


def test_check_provider_fake_and_registered_need_no_setup(dummy):
    assert check_provider("fake")[0] is True
    assert check_provider("dummy")[0] is True


def test_check_provider_unknown():
    ok, message = check_provider("no-such-provider")
    assert ok is False
    assert message == "provider 'no-such-provider' is not installed (entry point group wynd.providers)"


def test_check_provider_anthropic_reads_the_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert check_provider("anthropic") == (False, "ANTHROPIC_API_KEY is not set")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert check_provider("anthropic") == (True, "ANTHROPIC_API_KEY is set")


def _fake_sdk_location(monkeypatch, root: Path, *, bundled: bool) -> Path:
    import claude_agent_sdk

    package = root / "claude_agent_sdk"
    (package / "_bundled").mkdir(parents=True)
    cli = package / "_bundled" / "claude"
    if bundled:
        cli.write_text("#!/bin/sh\n")
    monkeypatch.setattr(claude_agent_sdk, "__file__", str(package / "__init__.py"))
    return cli


def test_check_provider_claude_code_ready_with_the_bundled_cli(monkeypatch, tmp_path: Path):
    cli = _fake_sdk_location(monkeypatch, tmp_path, bundled=True)
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: None)
    ok, message = check_provider("claude-code")
    assert ok is True
    assert str(cli) in message and "claude-agent-sdk" in message


def test_check_provider_claude_code_falls_back_to_claude_on_path(monkeypatch, tmp_path: Path):
    _fake_sdk_location(monkeypatch, tmp_path, bundled=False)
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: "/opt/bin/claude" if name == "claude" else None)
    ok, message = check_provider("claude-code")
    assert ok is True
    assert "/opt/bin/claude" in message


def test_check_provider_claude_code_without_a_cli(monkeypatch, tmp_path: Path):
    _fake_sdk_location(monkeypatch, tmp_path, bundled=False)
    monkeypatch.setattr(claude_code.shutil, "which", lambda name: None)
    ok, message = check_provider("claude-code")
    assert ok is False
    assert "no Claude Code CLI was found" in message


def test_check_provider_claude_code_without_the_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)     # makes `import claude_agent_sdk` fail
    ok, message = check_provider("claude-code")
    assert ok is False
    assert "needs claude-agent-sdk" in message

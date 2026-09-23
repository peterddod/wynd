"""Test doubles for RT-STEP tests (PLAN §0 rule 3): the step-package loader (RT-WORKER), the agentic class checks
(RT-LOOP) and tier resolution (RT-PROVIDERS) are coded against their PLAN contracts and replaced here, so these tests
exercise RT-STEP's own code whatever state those units are in."""

from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wynd.runtime.errors import StepFailure
from wynd.runtime.handle import StepCache
from wynd.runtime.interface import class_doc, interface_of
from wynd.runtime.middleware import ChainResult, run_chain
from wynd.runtime.policy import CassetteConfig, ExecPolicy
from wynd.runtime.worker.protocol import RunStepParams
from wynd.spec.lockfiles import RetryPolicy, ToolSnapshot
from wynd.spec.workspace import step_module_name

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "steps"
DEFAULT_TIERS = {
    "claude-code": {"cheap": "haiku", "standard": "sonnet", "strong": "opus"},
    "fake": {"cheap": "fake", "standard": "fake", "strong": "fake"},
}


# --- wynd.runtime.worker.loader (PLAN §3.6, $DRAFTS/02 §5.2) -----------------------------------------------------

def mount_step_package(step_id: str, package_dir: str | Path) -> str:
    if "wynd_steps" not in sys.modules:
        spec = importlib.machinery.ModuleSpec("wynd_steps", None, is_package=True)
        sys.modules["wynd_steps"] = importlib.util.module_from_spec(spec)
    name = f"wynd_steps.{step_module_name(step_id)}"
    if name not in sys.modules:
        spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
        spec.submodule_search_locations = [str(package_dir)]
        sys.modules[name] = importlib.util.module_from_spec(spec)
    return name


def load_step_class(step_id: str, entrypoint: str, package_dir: str | Path | None) -> type:
    package = mount_step_package(step_id, package_dir)
    module, _, name = entrypoint.partition(":")
    cls = getattr(importlib.import_module(f"{package}.{module}"), name)
    interface_of(cls)
    return cls


def use_loader(monkeypatch) -> None:
    monkeypatch.setattr("wynd.runtime.worker.loader.mount_step_package", mount_step_package)
    monkeypatch.setattr("wynd.runtime.worker.loader.load_step_class", load_step_class)


# --- wynd.runtime.agentic.checks ($DRAFTS/03 §6.1) ---------------------------------------------------------------

def use_agentic_checks(monkeypatch) -> list[str]:
    """No-op class checks recording the checked class names; `describe_agentic` returns the instruction."""
    checked: list[str] = []
    monkeypatch.setattr("wynd.runtime.agentic.checks.check_agentic_class", lambda cls: checked.append(cls.__name__))
    monkeypatch.setattr(
        "wynd.runtime.agentic.checks.describe_agentic",
        lambda cls: {"instruction": class_doc(cls), "completed_by_loop": True},
    )
    return checked


# --- wynd.runtime.providers.resolve_model (PLAN §3.15) ------------------------------------------------------------

def resolve_model(provider: str, tier: str, registry: Any) -> str:
    tiers = DEFAULT_TIERS.get(provider)
    if tiers is None:
        raise StepFailure("config", f"provider {provider!r} is not installed")
    if tier not in tiers:
        raise StepFailure("config", f"provider {provider!r} has no model for tier {tier!r}")
    return tiers[tier]


def use_providers(monkeypatch) -> None:
    monkeypatch.setattr("wynd.runtime.providers.resolve_model", resolve_model)


# --- RT-TOOLS ToolSpec (`step_tools(cls)` items; `snapshot()` -> the lock's ToolSnapshot) ---------------------------

@dataclass(frozen=True)
class FakeToolSpec:
    snap: ToolSnapshot

    def snapshot(self) -> ToolSnapshot:
        return self.snap


# --- building and running chains ---------------------------------------------------------------------------------

def make_params(
    workspace: Path,
    inputs: dict[str, Any] | None = None,
    *,
    kind: str = "deterministic",
    retries: RetryPolicy | None = None,
    effects: tuple[str, ...] = (),
    context: dict[str, Any] | None = None,
    cassette: CassetteConfig | None = None,
    step_path: str = "step",
    **policy: Any,
) -> RunStepParams:
    return RunStepParams(
        run_id="run_test", step_path=step_path, step_run=1, step_id=f"p#{step_path}", inputs=inputs or {},
        context=context, workspace=str(workspace),
        policy=ExecPolicy(kind=kind, retries=retries or RetryPolicy(), effects=list(effects), **policy),
        cassette=cassette or CassetteConfig(),
    )


def chain(cls: type, params: RunStepParams, events: list | None = None, cache: StepCache | None = None) -> ChainResult:
    sink = [] if events is None else events
    return run_chain(cls, params, emit=sink.append, cache=cache or StepCache())

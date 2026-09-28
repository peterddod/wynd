"""Live (`@live`, run with WYND_LIVE=1; one small haiku call): an AgenticStep dispatched through a real `WorkerPool`
worker with the claude-code provider in record mode. Proves that the worker's stdio claim and per-run output capture
coexist with the Claude Agent SDK's own subprocess (PLAN §5.7 RT-WORKER). Auth is whatever the machine has."""

from __future__ import annotations

import os
import sys
import textwrap

import pytest

from wynd.runtime.policy import CassetteConfig, build_policy
from wynd.runtime.storage import registry_from_env
from wynd.runtime.worker.pool import WorkerPool
from wynd.runtime.worker.protocol import RunStepParams
from wynd.spec.lockfiles import StepLock
from wynd.spec.plan import PlanStep, PlanVenv, RunPlan

pytestmark = pytest.mark.live

CAPITAL = '''
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import AgenticStep


class Capital(AgenticStep):
    """Name the capital city of the given country (the city name only)."""

    class Input(BaseModel):
        country: str

    class Done(BaseModel):
        exit: Literal["done"] = "done"
        capital: str

    Output = Done

    def run(self, input: Input) -> Output: ...
'''


def test_worker_claude_code_live(tmp_path):
    package = tmp_path / "capital"
    package.mkdir()
    (package / "capital.py").write_text(textwrap.dedent(CAPITAL))
    lock = StepLock(name="capital", kind="agentic", entrypoint="capital:Capital", tier="cheap", thinking="low")
    step = PlanStep(id="p#capital", kind="agentic", entrypoint="capital:Capital", venv="v", package_dir=str(package),
                    lock=lock)
    plan = RunPlan(
        mode="local", root="p", provider="claude-code", venv_root=str(tmp_path / "venvs"),
        venvs=[PlanVenv(id="v", python=sys.executable, steps=[step.id])], steps={step.id: step}, processes={},
    )
    policy = build_policy("agentic", lock, default_provider=plan.provider, registry=registry_from_env(),
                          environ=os.environ)
    record_dir = tmp_path / "recorded"
    params = RunStepParams(
        run_id="run-worker-live", step_path="capital", step_run=1, step_id=step.id, inputs={"country": "France"},
        workspace=str(tmp_path / "workspace"), policy=policy,
        cassette=CassetteConfig(mode="record", dir=str(package / "cassettes"), record_dir=str(record_dir)),
    )
    events: list[dict] = []
    with WorkerPool(plan, cwd=str(tmp_path)) as pool:
        result = pool.dispatch(step.id, params, on_event=events.append, timeout=300)

    assert result.exit == "done", result.outputs
    assert "paris" in result.outputs["capital"].lower()
    assert (result.model.provider, result.model.tier) == ("claude-code", "cheap")
    assert "haiku" in result.model.model_id
    assert result.usage.calls >= 1 and not result.replayed
    assert events[0]["type"] == "worker.start"
    calls = [event for event in events if event["type"] == "model.call"]
    assert calls, [event["type"] for event in events]
    assert calls[-1]["cassette"] == "record" and calls[-1]["outcome"] == "valid"
    assert list(record_dir.glob("*.json")), "the recorded call was not written"

"""`self.runtime` (PLAN §5.2): cache, trace, env, log, and the one enforced effect (`http` needs `network`, §15.34)."""

import logging
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel

from support.rt_step_doubles import chain, make_params
from wynd.runtime.agentic.errors import MissingEnvVar
from wynd.runtime.errors import StepFailure
from wynd.runtime.handle import RuntimeHandle, StepCache, StepTrace
from wynd.runtime.http import HttpClient
from wynd.runtime.step import DeterministicStep


def _handle(events: list, *, effects=(), logger=None) -> RuntimeHandle:
    return RuntimeHandle(
        run_id="run_1", step_path="fetch", step_run=2, workspace=Path("/tmp/ws"),
        logger=logger or logging.getLogger("wynd.step.test_handle"), trace=StepTrace(events.append),
        cache=StepCache(), effects=effects,
    )


def test_step_cache_operations():
    cache = StepCache()
    assert cache.get("k") is None and cache.get("k", 5) == 5
    cache.set("k", [1])
    assert cache.get("k") == [1]
    built: list = []
    assert cache.get_or_set("x", lambda: built.append(1) or "value") == "value"
    assert cache.get_or_set("x", lambda: built.append(1) or "other") == "value"
    assert built == [1]
    cache.delete("k")
    cache.delete("missing")
    assert cache.get("k") is None and cache.get("x") == "value"
    cache.clear()
    assert cache.get("x") is None


def test_step_trace_events():
    events: list = []
    trace = StepTrace(events.append)
    trace.event("page", n=3, of=10)
    trace.emit("tool.call", tool="now", ok=True)
    assert events == [
        {"type": "step.event", "name": "page", "data": {"n": 3, "of": 10}},
        {"type": "tool.call", "tool": "now", "ok": True},
    ]


def test_http_needs_the_network_effect():
    with pytest.raises(StepFailure) as err:
        _handle([], effects=("filesystem",)).http
    assert (err.value.cause, err.value.message) == ("config", "step does not declare effects: [network]")
    client = HttpClient(timeout=3)
    handle = _handle([], effects=("network",))
    handle.http_client = client
    assert handle.http is client


class In(BaseModel):
    url: str


class Done(BaseModel):
    exit: Literal["done"] = "done"
    status: int


class Fetch(DeterministicStep):
    Input = In
    Output = Done

    def run(self, input):
        return Done(status=self.runtime.http.get(input.url).status)


def test_http_without_network_resolves_the_step_with_cause_config(tmp_path):
    result = chain(Fetch, make_params(tmp_path, {"url": "http://127.0.0.1:9/"}, effects=("filesystem",)))
    assert result.exit == "error"
    assert (result.outputs["cause"], result.outputs["message"]) == (
        "config", "step does not declare effects: [network]")


def test_env_reads_set_values_defaults_and_fails_loudly(monkeypatch):
    handle = _handle([])
    monkeypatch.setenv("WYND_T_SET", "value")
    monkeypatch.setenv("WYND_T_EMPTY", "")
    monkeypatch.delenv("WYND_T_UNSET", raising=False)
    assert handle.env("WYND_T_SET") == "value"
    assert handle.env("WYND_T_EMPTY", "fallback") == "fallback"
    assert handle.env("WYND_T_UNSET", "") == ""
    with pytest.raises(MissingEnvVar) as err:
        handle.env("WYND_T_UNSET")
    assert err.value.name == "WYND_T_UNSET"
    assert "WYND_T_UNSET is not set" in str(err.value)


def test_log_goes_through_the_logger_with_fields_as_json():
    records: list[logging.LogRecord] = []
    logger = logging.getLogger("wynd.step.test_handle.log")
    logger.setLevel(logging.DEBUG)
    handler = logging.Handler()
    handler.emit = records.append
    logger.addHandler(handler)
    try:
        handle = _handle([], logger=logger)
        handle.log("saved")
        handle.log("saved", path="/x", n=2)
    finally:
        logger.removeHandler(handler)
    assert [(r.levelname, r.getMessage()) for r in records] == [
        ("INFO", "saved"), ("INFO", 'saved {"n": 2, "path": "/x"}'),
    ]

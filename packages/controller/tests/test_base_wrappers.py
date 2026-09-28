"""`wynd base build|publish` wrappers (CTL-M2; PLAN §8.1, §15 item 50, `$DRAFTS/06 §5.15`)."""

from __future__ import annotations

import pytest

import wynd.process.base as process_base
from wynd.controller.base import build_base, publish_base
from wynd.controller.errors import Invalid, NotFound, Unavailable, VersionMismatch
from wynd.process.errors import ToolMissing, WyndProcessError
from wynd.runtime import __version__
from wynd.runtime.storage import registry_from_env


class Spy:
    def __init__(self, result=None, raises: Exception | None = None) -> None:
        self.result = result
        self.raises = raises
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kw):
        self.calls.append((args, kw))
        if self.raises is not None:
            raise self.raises
        return self.result


def log(line: str) -> None:
    pass


@pytest.fixture
def local_registry():
    registry_from_env().put("registries", "local", {"url": "localhost:5001/wynd"})


def test_build_base_delegates_to_the_process_builder(monkeypatch):
    spy = Spy([f"wynd-base:{__version__}-slim", f"wynd-base:{__version__}-alpine"])
    monkeypatch.setattr(process_base, "build_base", spy)

    refs = build_base(__version__, ("slim", "alpine"), log=log)

    assert refs == spy.result
    assert spy.calls == [((__version__, ["slim", "alpine"]), {"log": log})]


def test_publish_base_delegates_with_the_user_registry(monkeypatch, local_registry):
    spy = Spy([f"localhost:5001/wynd/wynd-base:{__version__}-slim"])
    monkeypatch.setattr(process_base, "publish_base", spy)

    refs = publish_base(__version__, ["slim"], "local", log=log)

    assert refs == spy.result
    [(args, kw)] = spy.calls
    assert args == (__version__, ["slim"], "local")
    assert kw["log"] is log
    assert kw["registry"].get("registries", "local") == {"url": "localhost:5001/wynd"}


@pytest.mark.parametrize("wrapper", ["build", "publish"])
def test_a_version_other_than_the_runtime_is_refused_before_building(monkeypatch, local_registry, wrapper):
    spy = Spy([])
    monkeypatch.setattr(process_base, "build_base", spy)
    monkeypatch.setattr(process_base, "publish_base", spy)

    with pytest.raises(VersionMismatch, match=f"does not match wynd-runtime {__version__}") as caught:
        if wrapper == "build":
            build_base("9.9.9", ["slim"], log=log)
        else:
            publish_base("9.9.9", ["slim"], "local", log=log)

    assert caught.value.exit == 3
    assert spy.calls == []


def test_publish_base_to_an_unknown_registry_is_not_found(monkeypatch):
    spy = Spy([])
    monkeypatch.setattr(process_base, "publish_base", spy)
    with pytest.raises(NotFound, match="unknown image registry 'nowhere'"):
        publish_base(__version__, ["slim"], "nowhere", log=log)
    assert spy.calls == []


@pytest.mark.parametrize("error, expected", [
    (ToolMissing("docker"), Unavailable),
    (WyndProcessError("docker buildx build failed: boom"), Unavailable),
    (ValueError("unknown base variant(s) debian (expected slim or alpine)"), Invalid),
])
def test_process_errors_become_controller_errors(monkeypatch, error, expected):
    monkeypatch.setattr(process_base, "build_base", Spy(raises=error))
    with pytest.raises(expected, match=str(error).split("(")[0].strip()):
        build_base(__version__, ["slim"], log=log)

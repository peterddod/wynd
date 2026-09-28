"""How wynd-spec/wynd-runtime get into every local venv (PLAN §6.4 RuntimeSource; `$DRAFTS/04 §6.3`, §18.4)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import wynd.process.venvs as venvs
from wynd.process.venvs import RuntimeSource, VenvGroup, detect_runtime_source, local_venv_id

REPO = Path(__file__).resolve().parents[3]


class FakeDist:
    def __init__(self, version: str, direct_url: dict | None) -> None:
        self.version = version
        self._direct_url = direct_url

    def read_text(self, name: str) -> str | None:
        assert name == "direct_url.json"
        return json.dumps(self._direct_url) if self._direct_url is not None else None


def editable(path: Path) -> dict:
    return {"url": path.as_uri(), "dir_info": {"editable": True}}


@pytest.fixture
def dists(monkeypatch):
    """`dists({"wynd-spec": FakeDist, "wynd-runtime": FakeDist})` replaces the installed distributions."""
    def install(table: dict[str, FakeDist]) -> None:
        monkeypatch.setattr(venvs.metadata, "distribution", lambda name: table[name])
        monkeypatch.setattr(venvs.metadata, "version", lambda name: table[name].version)

    return install


def test_editable_members_from_direct_url(dists, tmp_path):
    dists({"wynd-spec": FakeDist("0.1.0", editable(tmp_path / "spec")),
           "wynd-runtime": FakeDist("0.1.0", editable(tmp_path / "runtime"))})
    source = detect_runtime_source({})
    assert source == RuntimeSource("0.1.0", (tmp_path / "spec", tmp_path / "runtime"))
    assert source.install_args() == ["-e", str(tmp_path / "spec"), "-e", str(tmp_path / "runtime")]


def test_installed_wheels_are_pinned(dists):
    dists({"wynd-spec": FakeDist("0.3.1", None), "wynd-runtime": FakeDist("0.3.1", None)})
    source = detect_runtime_source({})
    assert source == RuntimeSource("0.3.1", None)
    assert source.install_args() == ["wynd-spec==0.3.1", "wynd-runtime==0.3.1"]
    assert source.descriptor() == {"version": "0.3.1", "editable": None, "pyprojects": None}


def test_a_non_editable_direct_url_is_pinned(dists, tmp_path):
    local_wheel = {"url": (tmp_path / "wynd_runtime.whl").as_uri(), "archive_info": {}}
    dists({"wynd-spec": FakeDist("0.1.0", editable(tmp_path / "spec")),
           "wynd-runtime": FakeDist("0.1.0", local_wheel)})
    assert detect_runtime_source({}) == RuntimeSource("0.1.0", None)


def test_override_with_a_monorepo_directory(dists, tmp_path):
    dists({"wynd-spec": FakeDist("0.1.0", None), "wynd-runtime": FakeDist("0.1.0", None)})
    source = detect_runtime_source({"WYND_RUNTIME_SOURCE": str(tmp_path)})
    assert source == RuntimeSource("0.1.0", (tmp_path / "packages/spec", tmp_path / "packages/runtime"))


def test_override_with_a_version(dists):
    dists({"wynd-spec": FakeDist("0.1.0", None), "wynd-runtime": FakeDist("0.1.0", None)})
    source = detect_runtime_source({"WYND_RUNTIME_SOURCE": "0.2.0"})
    assert source == RuntimeSource("0.2.0", None)
    assert source.install_args() == ["wynd-spec==0.2.0", "wynd-runtime==0.2.0"]


def test_this_monorepo_is_detected_as_editable():
    source = detect_runtime_source(os.environ)
    assert source.editable == (REPO / "packages/spec", REPO / "packages/runtime")


def test_a_runtime_dependency_change_rekeys_editable_venvs(tmp_path):
    for pkg in ("spec", "runtime"):
        (tmp_path / pkg).mkdir()
        (tmp_path / pkg / "pyproject.toml").write_text('[project]\ndependencies = ["pydantic>=2.7"]\n')
    source = RuntimeSource("0.1.0", (tmp_path / "spec", tmp_path / "runtime"))
    group = VenvGroup("k", (), ())
    before = local_venv_id(group, source)
    assert local_venv_id(group, source) == before
    (tmp_path / "runtime" / "pyproject.toml").write_text('[project]\ndependencies = ["pydantic>=2.8"]\n')
    assert local_venv_id(group, source) != before
    assert local_venv_id(group, RuntimeSource("0.1.0", None)) != local_venv_id(group, RuntimeSource("0.1.1", None))

"""Repository-wide pytest configuration: opt-in gates for slow/external tests and test-environment isolation.

Imports only the standard library and pytest.
"""

import os
from pathlib import Path

import pytest

OPT_IN = {"live": "WYND_LIVE", "docker": "WYND_DOCKER", "kube": "WYND_KUBE"}

UNSET_VARS = (
    "WYND_WORKSPACE",
    "WYND_DATA_DIR",
    "WYND_CASSETTE_MODE",
    "WYND_CASSETTE_RECORD_DIR",
    "WYND_CASSETTE_LITERALS",
    "WYND_EVENTS_FILE",
    "WYND_REGISTRY_JSON",
)
SET_VARS = {
    "WYND_JOB_RUNNER": "inprocess",
    "WYND_REGISTRY": "file",
    "GIT_AUTHOR_NAME": "Wynd Test",
    "GIT_AUTHOR_EMAIL": "test@wynd.invalid",
    "GIT_COMMITTER_NAME": "Wynd Test",
    "GIT_COMMITTER_EMAIL": "test@wynd.invalid",
    "GIT_CONFIG_NOSYSTEM": "1",
}
CREDENTIAL_VARS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


def pytest_configure(config):
    for mark, env in OPT_IN.items():
        config.addinivalue_line("markers", f"{mark}: opt-in; runs only with {env}=1")


def pytest_collection_modifyitems(config, items):
    for item in items:
        for mark, env in OPT_IN.items():
            if item.get_closest_marker(mark) and os.environ.get(env) != "1":
                item.add_marker(pytest.mark.skip(reason=f"{mark} test: set {env}=1 to run"))


def _under_testpaths(config, path: Path) -> bool:
    roots = [config.rootpath / p for p in config.getini("testpaths")]
    return any(path.is_relative_to(root) for root in roots)


@pytest.fixture(autouse=True)
def _wynd_isolation(request, monkeypatch, tmp_path_factory):
    """Keep repository tests away from the user's WYND_HOME, workspace, data dir and credentials.

    Applies only to items under the root `testpaths`; step suites run elsewhere (e.g. by `wynd test`) keep their env.
    """
    if not _under_testpaths(request.config, request.node.path):
        yield
        return
    monkeypatch.setenv("WYND_HOME", str(tmp_path_factory.mktemp("wynd-home")))
    for name in UNSET_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in SET_VARS.items():
        monkeypatch.setenv(name, value)
    if request.node.get_closest_marker("live") is None:
        for name in CREDENTIAL_VARS:
            monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(scope="session")
def shared_venvs(tmp_path_factory) -> Path:
    """One step-venv root per test session; workspace-copy fixtures symlink `<ws>/.wynd/venvs` to it."""
    path = tmp_path_factory.getbasetemp() / "venvs"
    path.mkdir(exist_ok=True)
    return path

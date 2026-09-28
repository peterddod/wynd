"""Base variant choice and the musl fallback (PLAN §6.5, §15 item 21; `$DRAFTS/04 §8.3`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from support.proc_build_fakes import FakeResolver

from wynd.process.build.variant import choose_base
from wynd.process.fragments import MergedEnv
from wynd.process.venvs import VenvGroup

PYPDF = VenvGroup("aaaa", ("pypdf>=6,<7",), ("p#read",))
PLAIN = VenvGroup("bbbb", (), ("p#save", "p#tag"))
LINKS = [Path("/wheels")]


def env(glibc: tuple[str, ...] = ()) -> MergedEnv:
    return MergedEnv(steps={}, system=(), glibc_required_by=glibc, providers=(), fragments=())


def choose(requested, merged, groups, resolver, platform="linux/arm64"):
    return choose_base(requested, merged, groups, resolver=resolver, platform=platform, version="0.1.0",
                       find_links=LINKS)


@pytest.fixture(autouse=True)
def default_repo(monkeypatch):
    monkeypatch.delenv("WYND_BASE_REPO", raising=False)


def test_slim_requested_gives_slim_without_resolving():
    resolver = FakeResolver()
    base = choose("debian-slim-python", env(("provider:claude-code",)), [PYPDF], resolver)
    assert base.model_dump() == {"requested": "debian-slim-python", "variant": "slim", "version": "0.1.0",
                                 "image": "wynd-base:0.1.0-slim", "reason": None}
    assert resolver.calls == []


def test_alpine_with_requires_glibc_falls_back_to_slim():
    resolver = FakeResolver()
    base = choose("alpine-python", env(("step:p#ocr", "provider:claude-code")), [PYPDF], resolver)
    assert (base.variant, base.image) == ("slim", "wynd-base:0.1.0-slim")
    assert base.reason == ("alpine-python requested; fell back to debian-slim-python: requires: glibc declared by "
                           "step:p#ocr, provider:claude-code")
    assert resolver.calls == []


def test_alpine_without_musl_wheels_falls_back_naming_the_venv_and_steps():
    resolver = FakeResolver(musl_fail=("pypdf",))
    base = choose("alpine-python", env(), [PLAIN, PYPDF], resolver, platform="linux/amd64")
    assert base.variant == "slim"
    assert base.reason == ("alpine-python requested; fell back to debian-slim-python: venv aaaa (steps p#read) has "
                           "no musl-compatible wheels: No solution found when resolving dependencies for "
                           "x86_64-unknown-linux-musl")
    assert [(c["requirements"], c["python_platform"], c["only_binary"], c["universal"], c["find_links"])
            for c in resolver.calls] == [
        (["wynd-spec==0.1.0", "wynd-runtime==0.1.0"], "x86_64-unknown-linux-musl", True, False, ["/wheels"]),
        (["pypdf>=6,<7", "wynd-spec==0.1.0", "wynd-runtime==0.1.0"], "x86_64-unknown-linux-musl", True, False,
         ["/wheels"]),
    ]


def test_alpine_that_resolves_everywhere_is_alpine(monkeypatch):
    monkeypatch.setenv("WYND_BASE_REPO", "ghcr.io/peterddod/wynd-base")
    resolver = FakeResolver()
    base = choose("alpine-python", env(), [PLAIN, PYPDF], resolver)
    assert base.model_dump() == {"requested": "alpine-python", "variant": "alpine", "version": "0.1.0",
                                 "image": "ghcr.io/peterddod/wynd-base:0.1.0-alpine", "reason": None}
    assert {c["python_platform"] for c in resolver.calls} == {"aarch64-unknown-linux-musl"}


def test_an_unknown_platform_is_an_error():
    with pytest.raises(ValueError, match="linux/s390x"):
        choose("alpine-python", env(), [PLAIN], FakeResolver(), platform="linux/s390x")

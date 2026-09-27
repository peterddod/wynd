"""The wynd-base image family: template goldens, refs, build/publish/ensure with fakes (PLAN §6.5; `$DRAFTS/04 §9`).

`WYND_UPDATE_GOLDEN=1` rewrites the goldens.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from support.proc_build_fakes import FakeImageBuilder, FakeResolver
from support.proc_env_workspaces import MemRegistry

from wynd.process import base
from wynd.process.errors import WyndProcessError
from wynd.process.venvs import RuntimeSource

GOLDEN = Path(__file__).parent / "golden"
EDITABLE = RuntimeSource("0.1.0", (Path("/src/packages/spec"), Path("/src/packages/runtime")))
PINNED = RuntimeSource("0.1.0", None)


def golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if os.environ.get("WYND_UPDATE_GOLDEN") == "1":
        path.write_text(text)
    assert text == path.read_text(), f"golden {name} differs (WYND_UPDATE_GOLDEN=1 rewrites it)"


@pytest.fixture(autouse=True)
def default_repo(monkeypatch):
    monkeypatch.delenv("WYND_BASE_REPO", raising=False)


class SourceResolver(FakeResolver):
    """Builds a placeholder wheel for a source tree (the monorepo's spec/runtime are not at `/src`)."""

    def build_wheel(self, src, out_dir):
        self.calls.append({"op": "build_wheel", "src": str(src)})
        wheel = Path(out_dir) / f"{Path(src).name}-0.1.0-py3-none-any.whl"
        wheel.write_bytes(b"wheel")
        return wheel


class SnapshotBuilder(FakeImageBuilder):
    """Also snapshots the build context it sees (the context is deleted afterwards)."""

    def build(self, req, log):
        self.snapshot = {p.relative_to(req.context).as_posix(): p.read_text() for p in req.context.rglob("*")
                         if p.is_file() and p.suffix != ".whl"}
        self.wheels = sorted(p.name for p in (req.context / "wheels").iterdir())
        return super().build(req, log)


def test_base_slim_golden():
    golden("base-slim.Dockerfile", base.render_base_dockerfile("0.1.0", "slim"))


def test_base_alpine_golden():
    golden("base-alpine.Dockerfile", base.render_base_dockerfile("0.1.0", "alpine"))


def test_the_base_environment_and_user():
    text = base.render_base_dockerfile("0.1.0", "slim")
    for line in ("WYND_DATA_DIR=/var/lib/wynd", "WYND_HOME=/var/lib/wynd/home", "WYND_REGISTRY=env",
                 "WYND_PLAN=/opt/wynd/process/process.lock.yaml", "UV_CACHE_DIR=/var/cache/uv",
                 "FROM ghcr.io/astral-sh/uv:0.10.7 AS uv", "--uid 10001", "USER wynd",
                 'ENTRYPOINT ["/opt/wynd/supervisor/bin/wynd-supervisor"]', 'CMD ["serve"]'):
        assert line in text
    assert "@" not in text.replace("@VERSION", "").replace("WYND", "")    # no token left


def test_base_image_ref(monkeypatch):
    assert base.base_image_ref("0.1.0", "slim") == "wynd-base:0.1.0-slim"
    assert base.base_image_ref("0.1.0", "alpine", "localhost:5001/wynd/wynd-base") == \
        "localhost:5001/wynd/wynd-base:0.1.0-alpine"
    monkeypatch.setenv("WYND_BASE_REPO", "ghcr.io/peterddod/wynd-base")
    assert base.base_image_ref("0.1.0", "slim") == "ghcr.io/peterddod/wynd-base:0.1.0-slim"


def test_build_base_from_the_monorepo_vendors_fresh_wheels():
    resolver, builder, lines = SourceResolver(), SnapshotBuilder(present=()), []
    refs = base.build_base("0.1.0", ["slim", "alpine"], log=lines.append, image_builder=builder, runtime=EDITABLE,
                           resolver=resolver)
    assert refs == ["wynd-base:0.1.0-slim", "wynd-base:0.1.0-alpine"]
    assert [c["src"] for c in resolver.calls if c["op"] == "build_wheel"] == [
        "/src/packages/spec", "/src/packages/runtime"]
    compile_call = next(c for c in resolver.calls if c["op"] == "compile")
    assert compile_call["requirements"] == ["wynd-runtime==0.1.0"] and compile_call["universal"]
    assert compile_call["find_links"][0].endswith("/wheels")
    assert [(r.tags, r.platforms, r.push, r.dockerfile.name) for r in builder.requests] == [
        (("wynd-base:0.1.0-slim",), (), False, "slim.Dockerfile"),
        (("wynd-base:0.1.0-alpine",), (), False, "alpine.Dockerfile")]
    assert builder.snapshot["runtime-requirements.txt"] == "wynd-runtime==0.1.0\n"
    assert builder.wheels == ["runtime-0.1.0-py3-none-any.whl", "spec-0.1.0-py3-none-any.whl"]
    assert builder.snapshot["alpine.Dockerfile"] == base.render_base_dockerfile("0.1.0", "alpine")
    assert not builder.requests[0].context.exists()                                   # the context is removed


def test_build_base_from_an_index_has_no_wheels():
    resolver, builder = FakeResolver(), SnapshotBuilder(present=())
    base.build_base("0.1.0", ["slim"], log=lambda line: None, image_builder=builder, runtime=PINNED,
                    resolver=resolver, repo="reg.example/wynd-base", platforms=["linux/amd64"])
    assert builder.wheels == []
    assert [c["op"] for c in resolver.calls] == ["compile"] and resolver.calls[0]["find_links"] == []
    assert (builder.requests[0].tags, builder.requests[0].platforms) == (("reg.example/wynd-base:0.1.0-slim",),
                                                                         ("linux/amd64",))


def test_build_base_refuses_a_version_other_than_the_source_tree():
    with pytest.raises(WyndProcessError, match="source tree is wynd-runtime 0.1.0; cannot build base 0.2.0"):
        base.build_base("0.2.0", ["slim"], log=print, image_builder=FakeImageBuilder(), runtime=EDITABLE,
                        resolver=FakeResolver())
    with pytest.raises(ValueError, match="debian"):
        base.build_base("0.1.0", ["debian"], log=print, image_builder=FakeImageBuilder(), runtime=EDITABLE,
                        resolver=FakeResolver())


def test_publish_base_logs_in_and_pushes_both_platforms(monkeypatch):
    monkeypatch.setenv("REG_USER", "peter")
    monkeypatch.setenv("REG_TOKEN", "t0ken")
    captured = {}
    monkeypatch.setattr(base, "build_base", lambda version, variants, **kw: captured.update(
        version=version, variants=variants, **kw) or ["x"])
    registry = MemRegistry({"registries": {"ghcr": {"name": "ghcr", "url": "ghcr.io/peterddod",
                                                    "username_env": "REG_USER", "password_env": "REG_TOKEN"}}})
    builder = FakeImageBuilder()
    assert base.publish_base("0.1.0", ["slim"], "ghcr", registry=registry, log=print, image_builder=builder) == ["x"]
    assert builder.calls == [("login", "ghcr.io", "peter", "t0ken")]
    assert (captured["repo"], captured["platforms"], captured["push"], captured["image_builder"]) == (
        "ghcr.io/peterddod/wynd-base", ("linux/amd64", "linux/arm64"), True, builder)


def test_publish_base_to_an_unknown_registry_fails():
    with pytest.raises(WyndProcessError, match="unknown image registry 'nope'"):
        base.publish_base("0.1.0", ["slim"], "nope", registry=MemRegistry(), log=print,
                          image_builder=FakeImageBuilder())


def test_ensure_base(monkeypatch):
    built = []
    monkeypatch.setattr(base, "build_base", lambda version, variants, **kw: built.append((variants, kw["repo"])))
    monkeypatch.setattr("wynd.process.venvs.detect_runtime_source", lambda environ: EDITABLE)
    base.ensure_base("wynd-base:0.1.0-slim", "0.1.0", "slim", image_builder=FakeImageBuilder(), log=print)
    assert built == []                                                               # present locally
    base.ensure_base("ghcr.io/x/wynd-base:0.1.0-slim", "0.1.0", "slim", image_builder=FakeImageBuilder(()),
                     log=print)
    base.ensure_base("localhost:5001/wynd-base:0.1.0-slim", "0.1.0", "slim", image_builder=FakeImageBuilder(()),
                     log=print)
    assert built == []                                                               # BuildKit pulls these
    base.ensure_base("wynd-base:0.1.0-alpine", "0.1.0", "alpine", image_builder=FakeImageBuilder(()), log=print)
    assert built == [(["alpine"], "wynd-base")]

    monkeypatch.setattr("wynd.process.venvs.detect_runtime_source", lambda environ: PINNED)
    with pytest.raises(WyndProcessError, match="run wynd base build 0.1.0 or set WYND_BASE_REPO"):
        base.ensure_base("wynd-base:0.1.0-slim", "0.1.0", "slim", image_builder=FakeImageBuilder(()), log=print)

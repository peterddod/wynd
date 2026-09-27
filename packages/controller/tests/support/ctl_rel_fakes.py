"""CTL-REL test doubles: `FakeServing` and `FakeTriggers` (the serving and trigger backends), `install_backends` (puts
them where `ctx.serving`/`ctx.triggers` are cached) and `put_build` (a stored build of a process at a commit).

`FakeServing.ensure` answers `http://serving.test/<release id>` (marked ready on the `FakeRunApi` when given) and
records `(method, release id, env)`; `fail` makes `ensure` raise it; `supported_bindings` can be narrowed.
`FakeTriggers` records every call.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

IMAGE = "wynd/p1:0123456789ab"


class FakeServing:
    name = "fake"

    def __init__(self, api: Any = None) -> None:
        self.api = api
        self.supported_bindings = frozenset({"value", "from_env"})
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []
        self.manifests: list[Any] = []
        self.states: dict[str, str] = {}
        self.fail: Exception | None = None

    def ensure(self, release, manifest, env: Mapping[str, str]) -> str:
        self.calls.append(("ensure", release.id, dict(env)))
        self.manifests.append(manifest)
        if self.fail is not None:
            raise self.fail
        url = f"http://serving.test/{release.id}"
        if self.api is not None:
            self.api.ready.add(url)
        self.states[release.id] = "serving"
        return url

    def status(self, release) -> tuple[str, str | None]:
        return self.states.get(release.id, "stopped"), None

    def remove(self, release_id: str) -> None:
        self.calls.append(("remove", release_id, None))
        self.states.pop(release_id, None)

    def ensured(self) -> list[tuple[str, dict[str, str]]]:
        return [(release_id, env) for method, release_id, env in self.calls if method == "ensure"]

    def removed(self) -> list[str]:
        return [release_id for method, release_id, _ in self.calls if method == "remove"]


class FakeTriggers:
    name = "fake-triggers"

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def install(self, release) -> None:
        self.calls.append(("install", release.id))

    def remove(self, release_id: str) -> None:
        self.calls.append(("remove", release_id))

    def reconcile(self, releases: Sequence) -> None:
        self.calls.append(("reconcile", [r.id for r in releases]))

    def start(self, ctl) -> None:
        self.calls.append(("start", None))

    def stop(self) -> None:
        self.calls.append(("stop", None))


def install_backends(ctl, serving: Any, triggers: Any) -> None:
    """Replace the controller's lazily selected serving and trigger backends (cached properties on a frozen
    dataclass: the instance dict takes precedence)."""
    vars(ctl.ctx).update(serving=serving, triggers=triggers)


def put_build(ctl, pid: str, commit: str, manifest, *, image: str = IMAGE, pushed: Sequence[str] = ()):
    """Store a build of `pid` at `commit` in the controller's artefact store; -> its `BuildInfo`."""
    from wynd.process.artefacts import BuildInfo

    staged = ctl.ctx.state_dir / "tmp" / f"staged-{pid}-{commit[:7]}"
    staged.mkdir(parents=True)
    info = BuildInfo(process=pid, commit=commit, source_sha=commit, job_id=None, process_hash="sha256:" + "0" * 64,
                     image=image, image_id=None, image_digest="sha256:" + "d" * 64, pushed=list(pushed), base={},
                     manifest=manifest, created_at=datetime(2026, 9, 22, tzinfo=UTC), dir="")
    return ctl.ctx.artefacts.put_build(staged, info)

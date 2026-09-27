"""`wynd base build|publish` wrappers (PLAN §8.1, §15 item 50; `$DRAFTS/06 §5.15`).

Plain functions, not jobs: they delegate to `wynd.process.base` and raise `VersionMismatch` unless
`version == wynd.runtime.__version__`. They need no workspace; `publish_base` reads the image registry entry from
the user registry (`registry_from_env()`). Process errors surface as controller errors: an unknown registry name is
`NotFound`, a missing tool or a failed build `Unavailable`, an unknown variant `Invalid`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal

from wynd.controller.errors import Invalid, NotFound, Unavailable, VersionMismatch


def build_base(version: str, variants: Sequence[Literal["slim", "alpine"]], *, log: Callable[[str], None]) -> list[str]:
    """-> image references built."""
    from wynd.process import base

    _check_version(version)
    return _delegate(lambda: base.build_base(version, list(variants), log=log))


def publish_base(
    version: str, variants: Sequence[Literal["slim", "alpine"]], registry_name: str, *, log: Callable[[str], None]
) -> list[str]:
    """-> image references pushed."""
    from wynd.process import base
    from wynd.runtime.storage import registry_from_env

    _check_version(version)
    registry = registry_from_env()
    if registry.get("registries", registry_name) is None:
        raise NotFound(f"unknown image registry '{registry_name}'",
                       hint="add it with `wynd registry add <name> <url>`")
    return _delegate(lambda: base.publish_base(version, list(variants), registry_name, registry=registry, log=log))


def _check_version(version: str) -> None:
    from wynd.runtime import __version__

    if version != __version__:
        raise VersionMismatch(f"base version {version} does not match wynd-runtime {__version__}",
                              hint=f"the runtime and its base image share a version: use {__version__}")


def _delegate(call: Callable[[], list[str]]) -> list[str]:
    from wynd.process.errors import WyndProcessError

    try:
        return call()
    except WyndProcessError as err:
        raise Unavailable(str(err)) from err
    except ValueError as err:                       # unknown variant or image builder
        raise Invalid(str(err)) from err

"""`python -m wynd.runtime.describe <package_dir> <entrypoint>`: print a step package's `PackageDescription` JSON
(`{kind, interface, context, tools, mcp, exit_codes, doc}`), mounting the package like the worker (PLAN §5.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


class PackageDescription:
    """`{kind: StepKind, interface: Interface, context: list[str], tools: list[ToolSnapshot],
    mcp: list[{server, allow}], exit_codes: dict[str, str] | None, doc: str}`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.1")


def describe_package(package_dir: str | Path, entrypoint: str) -> PackageDescription:
    raise NotImplementedError("PLAN §5.1")


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError("PLAN §5.1")


if __name__ == "__main__":
    raise SystemExit(main())

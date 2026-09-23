"""`python -m wynd.runtime.describe <package_dir> <entrypoint>`: print a step package's `PackageDescription` JSON
(`{kind, interface, context, tools, mcp, exit_codes, doc}`), mounting the package like the worker (PLAN §5.1).

Used by `wynd validate --sync-interfaces`, the test runner's drift check and the compiler; it runs in the step's venv.
Exit 0 with the JSON on stdout; 1 when the package cannot be described (traceback on stderr); 2 on bad usage.
"""

from __future__ import annotations

import contextlib
import sys
import traceback
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from wynd.runtime.errors import StepDefinitionError
from wynd.runtime.interface import class_doc, interface_of
from wynd.runtime.step import Step, step_kind
from wynd.spec.interface import Interface
from wynd.spec.lockfiles import StepKind, ToolSnapshot

USAGE = "usage: python -m wynd.runtime.describe <package_dir> <entrypoint>"


class PackageDescription(BaseModel):
    kind: StepKind
    interface: Interface
    context: list[str] = []
    tools: list[ToolSnapshot] = []
    mcp: list[dict[str, Any]] = []            # [{"server", "allow"}]
    exit_codes: dict[str, str] | None = None  # shell steps only
    doc: str = ""


def describe_package(package_dir: str | Path, entrypoint: str) -> PackageDescription:
    from wynd.runtime.worker.loader import load_step_class

    package_dir = Path(package_dir).resolve()
    return describe_class(load_step_class(str(package_dir), entrypoint, package_dir))


def describe_class(cls: type[Step]) -> PackageDescription:
    kind = step_kind(cls)
    if kind == "process":
        raise StepDefinitionError(f"{cls.__qualname__} is a ProcessStep; processes are not step packages")
    extra: dict[str, Any] = {}
    match kind:
        case "agentic":
            from wynd.runtime.tools.decorator import step_tools

            extra = {
                "context": list(cls.context),
                "tools": [spec.snapshot() for spec in step_tools(cls)],    # @tool methods, then cls.tools
                "mcp": [{"server": server.name, "allow": list(server.allow)} for server in cls.mcp],
            }
        case "shell":
            extra = {"exit_codes": {str(code): exit for code, exit in cls.exit_codes.items()}}
    return PackageDescription(kind=kind, interface=interface_of(cls).interface, doc=class_doc(cls) or "", **extra)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        with contextlib.redirect_stdout(sys.stderr):     # prints at import time must not corrupt the JSON
            description = describe_package(args[0], args[1])
    except Exception:  # noqa: BLE001 — the CLI boundary: any failure is reported, never a partial JSON
        traceback.print_exc()
        return 1
    print(description.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

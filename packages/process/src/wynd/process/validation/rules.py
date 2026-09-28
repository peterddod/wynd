"""Child, provider and package rules (PLAN §6.1, §6.3 pass 7; owner PROC-VAL).

`W203`–`W205` (child declares a different `env.base`/`provider`/`latency`), `W206` (agentic step using an
AgentProvider in a `latency: fast` root, closure-wide), `W207` (unknown provider), `W128`, `W129`, `I130`, `W202`,
`E128`, and runtime lint `L001`–`L005` (errors) over every non-test `.py` file of each compiled step package in the
closure (`wynd.runtime.lint.check_step_module`).

A child's `env.base`, `provider` and `latency` never apply (SPEC §3.2): the ROOT's effective values do, so W203–W205
compare against the root. W207 covers the providers that are effective: the root's default and the agentic steps'
`provider:` overrides.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from typing import TYPE_CHECKING

from wynd.spec.base import DEFAULT_PROVIDER
from wynd.spec.errors import Diagnostic
from wynd.spec.workspace import RESERVED_PROCESS_SEGMENTS

from ..workspace import PYPROJECT_FILE, diagnostic

if TYPE_CHECKING:
    from wynd.runtime.providers import ProviderInfo

    from ..loader import LoadedProcess, StepPackage
    from ..workspace import Tree

ProviderCatalog = Callable[[str], "ProviderInfo"]

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")


def _short(value: str | None) -> str:
    return value.removeprefix("sha256:")[:12] if value else "none"


def binding_rules(lp: LoadedProcess, root: LoadedProcess) -> list[Diagnostic]:
    """Per process: E128 (reserved last id segment), per step binding W202/W129/I130, per `process:` binding
    W203–W205 against the root's effective values."""
    pid, source = lp.id, lp.doc._source
    out: list[Diagnostic] = []
    segment = pid.rpartition("/")[2]
    if segment in RESERVED_PROCESS_SEGMENTS:
        out.append(diagnostic("E128", source=source, loc=("name",), process=pid, id=pid, seg=segment,
                              reserved=", ".join(sorted(RESERVED_PROCESS_SEGMENTS))))

    parent = root.doc
    inherited = (
        ("W203", "env.base", parent.env.base),
        ("W204", "provider", parent.effective_provider),
        ("W205", "latency", parent.latency or "normal"),
    )
    for key, rs in lp.steps.items():
        loc = ("steps", key)
        package = rs.package
        if package is not None:
            match package.interface.source:
                case "examples":
                    out.append(diagnostic("W202", source=source, loc=loc, process=pid, alias=key))
                case "none" if package.lock is not None:
                    out.append(diagnostic("W129", source=source, loc=loc, process=pid, id=package.id,
                                          pid=root.id))
            if package.stale:
                out.append(diagnostic("I130", source=source, loc=loc, process=pid, alias=key,
                                      old=_short(package.lock.proto_hash), new=_short(package.proto_hash)))
        child = lp.children.get(rs.child) if rs.child is not None else None
        if child is None:
            continue
        declared = {
            "env.base": child.doc.env.base if "base" in child.doc.env.model_fields_set else None,
            "provider": child.doc.provider,
            "latency": child.doc.latency,
        }
        for code, field, parent_value in inherited:
            value = declared[field]
            if value is not None and value != parent_value:
                out.append(diagnostic(code, source=source, loc=loc, process=pid, alias=key, child=rs.child,
                                      field=field, child_value=value, parent_value=parent_value))
    return out


def _agentic(root: LoadedProcess) -> list[tuple[str, StepPackage]]:
    """(process id, package) of every compiled agentic step in the closure, each package once."""
    seen: dict[str, tuple[str, StepPackage]] = {}
    for pid, lp in root.closure_processes().items():
        for rs in lp.steps.values():
            package = rs.package
            if package is not None and package.lock is not None and package.lock.kind == "agentic":
                seen.setdefault(package.id, (pid, package))
    return list(seen.values())


def provider_rules(root: LoadedProcess, providers: ProviderCatalog) -> list[Diagnostic]:
    """Closure-wide: W207 for an unknown effective provider, W206 for AgentProvider steps in a `latency: fast` root."""
    doc, source = root.doc, root.doc._source
    out: list[Diagnostic] = []
    kinds: dict[str, str | None] = {}

    def kind(name: str) -> str | None:
        if name not in kinds:
            try:
                kinds[name] = providers(name).kind
            except KeyError:
                kinds[name] = None
        return kinds[name]

    default = doc.provider or DEFAULT_PROVIDER
    if kind(default) is None:
        out.append(diagnostic("W207", source=source, loc=("provider",), process=root.id, provider=default))
    for pid, package in _agentic(root):
        override = package.lock.provider
        if override is not None and kind(override) is None:
            out.append(diagnostic("W207", source=package.lock._source, loc=("provider",), process=pid,
                                  provider=override))
        effective = override or default
        if doc.latency == "fast" and kind(effective) == "agent":
            out.append(diagnostic("W206", source=source, loc=("latency",), process=root.id, id=package.id,
                                  provider=effective))
    return out


def _names(requirements: list) -> list[str]:
    """Distribution names of PEP 508 requirement strings."""
    return [m.group(0) for r in requirements if (m := _NAME.match(str(r).strip())) is not None]


def _pyproject_names(text: str) -> list[str]:
    try:
        project = tomllib.loads(text).get("project", {})
    except tomllib.TOMLDecodeError:
        return []
    return _names(project.get("dependencies", [])) if isinstance(project, dict) else []


def _pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def package_rules(root: LoadedProcess, tree: Tree) -> list[Diagnostic]:
    """Per compiled package in the closure (each once): W128 (pyproject dependency missing from the lock's
    `fragment.deps`) and the runtime lint L001–L005 over every non-test `.py` file."""
    from wynd.runtime.lint import check_step_module

    out: list[Diagnostic] = []
    seen: set[str] = set()
    files = tree.files()
    for pid, lp in root.closure_processes().items():
        for rs in lp.steps.values():
            package = rs.package
            if package is None or package.lock is None or package.id in seen:
                continue
            seen.add(package.id)
            pyproject = f"{package.dir}/{PYPROJECT_FILE}"
            if pyproject in files:
                locked = {_pep503(name) for name in _names(package.lock.fragment.deps)}
                for name in _pyproject_names(tree.read_bytes(pyproject).decode("utf-8", errors="replace")):
                    if _pep503(name) not in locked:
                        out.append(diagnostic("W128", file=pyproject, process=pid, id=package.id, name=name))
            prefix = f"{package.dir}/"
            for path in sorted(files):
                name = path.rpartition("/")[2]
                if not path.startswith(prefix) or not name.endswith(".py") or name.startswith("test_"):
                    continue
                text = tree.read_bytes(path).decode("utf-8", errors="replace")
                try:
                    issues = check_step_module(text, path)
                except SyntaxError:
                    continue  # the worker reports it at import (cause `import`); lint has no code for it
                out += [Diagnostic(i.severity, i.code, i.message, file=path, line=i.line, column=i.col, process=pid)
                        for i in issues]
    return out

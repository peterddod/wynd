"""Importing step packages as `wynd_steps.<step_module_name(step_id)>` (PLAN §3.6; `$DRAFTS/02 §5.2`).

Local mode mounts the package directory under that name; image mode imports the installed wheel, whose files land in
`site-packages/wynd_steps/<module name>/` (`wynd_steps` is a PEP 420 namespace). The import path is the same in both
modes, two packages with the same directory name never collide, and intra-package imports are relative.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path

from wynd.runtime.errors import StepDefinitionError
from wynd.runtime.interface import interface_of
from wynd.runtime.step import Step, step_kind
from wynd.spec.workspace import step_module_name

STEPS_NS = "wynd_steps"


def mount_step_package(step_id: str, package_dir: str | Path) -> str:
    """Register `package_dir` as package `wynd_steps.<step_module_name(step_id)>`; return its dotted name.
    Idempotent: a name that is already imported is returned as is."""
    namespace = _steps_namespace()
    name = f"{STEPS_NS}.{step_module_name(step_id)}"
    if name in sys.modules:
        return name
    path = str(Path(package_dir).resolve())
    init = Path(path) / "__init__.py"
    if init.is_file():
        spec = importlib.util.spec_from_file_location(name, init, submodule_search_locations=[path])
    else:
        spec = ModuleSpec(name, None, is_package=True)
        spec.submodule_search_locations = [path]
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        if spec.loader is not None:
            spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    setattr(namespace, name.rpartition(".")[2], module)
    return name


def load_step_class(step_id: str, entrypoint: str, package_dir: str | Path | None) -> type[Step]:
    """Import `"<module>:<Class>"` from the step package (mounted when `package_dir` is given, installed otherwise)
    and check the class against the step contract (StepDefinitionError)."""
    module_name, sep, class_name = entrypoint.partition(":")
    if not (sep and module_name and class_name):
        raise StepDefinitionError(f"entrypoint {entrypoint!r} of step {step_id} must be '<module>:<Class>'")
    if package_dir is None:
        package = f"{STEPS_NS}.{step_module_name(step_id)}"
    else:
        package = mount_step_package(step_id, package_dir)
    module = importlib.import_module(f"{package}.{module_name}")
    cls = getattr(module, class_name, None)
    if not (isinstance(cls, type) and issubclass(cls, Step)):
        raise StepDefinitionError(f"entrypoint {entrypoint!r} of step {step_id} is not a Step subclass")
    if step_kind(cls) == "process":
        raise StepDefinitionError(f"entrypoint {entrypoint!r} of step {step_id} is a ProcessStep, not a step package")
    interface_of(cls)
    return cls


def _steps_namespace():
    """The `wynd_steps` package: the installed PEP 420 namespace when there is one, else an empty one."""
    module = sys.modules.get(STEPS_NS)
    if module is not None:
        return module
    try:
        return importlib.import_module(STEPS_NS)
    except ModuleNotFoundError as err:
        if err.name != STEPS_NS:
            raise
    module = importlib.util.module_from_spec(ModuleSpec(STEPS_NS, None, is_package=True))
    sys.modules[STEPS_NS] = module
    return module

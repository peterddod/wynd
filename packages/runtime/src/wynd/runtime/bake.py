"""Single-venv run of a baked process (`$DRAFTS/04 §8.9`): called by the zipapp bootstrap with the extracted
`site`; every worker is this interpreter.

Usage of a baked app: `app.pyz '<inputs JSON>'`, or the JSON on stdin (no argument or `-`). It prints
`{"run_id", "exit", "outputs"}` as JSON and exits 0 for a declared exit, 1 for `$exit.error`, 2 for bad inputs and
3 when the env manifest's check fails (the `wynd` CLI exit codes, PLAN §3.22).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

ENV_MANIFEST = "process.env.yaml"          # next to plan.json in `_wynd_bake/`
DATA_DIR = "data"                          # default run data root, next to `site` (WYND_DATA_DIR overrides)
USAGE = "usage: <app.pyz> ['<inputs JSON>' | -]  (inputs are read from stdin when omitted)"


def main(plan_path: Path, site: Path, argv: list[str]) -> int:
    from wynd.runtime.edges import WorkerEdgeChecker
    from wynd.runtime.errors import InvalidProcessInputs
    from wynd.runtime.executor.engine import Executor
    from wynd.runtime.storage import stores_from_env
    from wynd.runtime.worker.pool import WorkerPool
    from wynd.spec.plan import RunPlan

    plan_path, site = Path(plan_path), Path(site)
    try:
        inputs = _read_inputs(argv)
    except ValueError as err:
        print(f"{err}\n{USAGE}", file=sys.stderr)
        return 2
    plan = RunPlan.model_validate_json(plan_path.read_text())
    venv = plan.venvs[0].model_copy(update={"python": sys.executable})
    plan = plan.model_copy(update={"venvs": [venv]})
    env = {**os.environ, "PYTHONPATH": str(site)}
    if not _env_ok(plan_path.parent / ENV_MANIFEST, env):
        return 3
    inputs = _absolute_paths(plan, inputs)

    stores = stores_from_env(env, data_dir=site.parent / DATA_DIR)
    pool = WorkerPool(plan, env=env)
    try:
        executor = Executor(plan, pool, stores, env=env, edge_checker=WorkerEdgeChecker(pool, plan))
        result = executor.run(inputs)
    except InvalidProcessInputs as err:
        print(str(err), file=sys.stderr)
        return 2
    finally:
        pool.close()
    print(json.dumps({"run_id": result.run_id, "exit": result.exit, "outputs": result.outputs}, default=str))
    return 0 if result.status == "succeeded" else 1


def _read_inputs(argv: list[str]) -> dict[str, Any]:
    match argv:
        case [] | ["-"]:
            text = sys.stdin.read()
        case [text]:
            pass
        case _:
            raise ValueError(f"expected at most one argument, got {len(argv)}")
    try:
        inputs = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as err:
        raise ValueError(f"inputs are not valid JSON: {err}") from None
    if not isinstance(inputs, dict):
        raise ValueError("inputs must be a JSON object")
    return inputs


def _env_ok(manifest_path: Path, env: dict[str, str]) -> bool:
    """The baked env manifest checked as a host run (`mode="local"`); errors and warnings go to stderr."""
    from wynd.spec.env_manifest import EnvManifest, check_env
    from wynd.spec.yamlio import load_model

    if not manifest_path.is_file():
        return True
    check = check_env(load_model(manifest_path, EnvManifest), env, mode="local")
    for diagnostic in check.diagnostics:
        print(f"{diagnostic.severity}[{diagnostic.code}]: {diagnostic.message}", file=sys.stderr)
    return check.ok


def _absolute_paths(plan: Any, inputs: dict[str, Any]) -> dict[str, Any]:
    """Relative values of `path`-typed root inputs are made absolute against the cwd (steps run in their own run
    workspace, so a relative path would resolve somewhere else)."""
    schema = plan.processes[plan.root].definition.interface().input
    out = dict(inputs)
    for name, prop in schema.get("properties", {}).items():
        value = out.get(name)
        if isinstance(value, str) and _is_path(prop) and not Path(value).is_absolute():
            out[name] = str(Path.cwd() / value)
    return out


def _is_path(prop: dict[str, Any]) -> bool:
    options = prop.get("anyOf", [prop])
    return any(option.get("format") == "path" for option in options)

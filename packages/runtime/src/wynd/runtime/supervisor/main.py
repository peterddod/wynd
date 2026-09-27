"""Console script `wynd-supervisor` (PLAN §3.17): `serve [--plan PATH] [--host] [--port]`, `env-check
[--manifest PATH]`, `schema`, `check-plan <process.lock.yaml>`."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from collections.abc import Mapping
from pathlib import Path

DEFAULT_PLAN = "/opt/wynd/process/process.lock.yaml"
DEFAULT_MANIFEST = "/opt/wynd/process/process.env.yaml"
DEFAULT_DATA_DIR = "/var/lib/wynd"

log = logging.getLogger("wynd.supervisor")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wynd-supervisor", description="Wynd in-image supervisor and run API.")
    commands = parser.add_subparsers(dest="command", required=True)
    serve_p = commands.add_parser("serve", help="serve the run API (the image entrypoint)")
    serve_p.add_argument("--plan", help=f"process.lock.yaml (default $WYND_PLAN or {DEFAULT_PLAN})")
    serve_p.add_argument("--host", help="listen address (default $WYND_HOST or 0.0.0.0)")
    serve_p.add_argument("--port", type=int, help="listen port (default $WYND_PORT or 8080)")
    check_p = commands.add_parser("env-check", help="check the environment against the env manifest")
    check_p.add_argument("--manifest", help=f"process.env.yaml (default $WYND_ENV_MANIFEST or {DEFAULT_MANIFEST})")
    commands.add_parser("schema", help="print the run API JSON Schema")
    plan_p = commands.add_parser("check-plan", help="start every venv worker and import every step")
    plan_p.add_argument("lock", help="process.lock.yaml")
    args = parser.parse_args(argv)

    env = os.environ
    match args.command:
        case "serve":
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
            return serve(Path(args.plan or env.get("WYND_PLAN") or DEFAULT_PLAN), host=args.host, port=args.port,
                         env=env)
        case "env-check":
            return env_check(Path(args.manifest or env.get("WYND_ENV_MANIFEST") or DEFAULT_MANIFEST), env)
        case "schema":
            from wynd.runtime.supervisor.schema import export_json_schema

            print(json.dumps(export_json_schema(), indent=2, sort_keys=True))
            return 0
        case "check-plan":
            return check_plan(Path(args.lock))
    return 2


def env_problems(manifest_path: Path, env: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """(errors, warnings) of `check_env(mode="image")`; a missing or unreadable manifest is an error."""
    from wynd.spec.env_manifest import check_env, load_env_manifest

    try:
        manifest = load_env_manifest(manifest_path)
    except Exception as err:  # noqa: BLE001 — any load failure is reported the same way
        return [f"cannot read env manifest {manifest_path}: {err}"], []
    result = check_env(manifest, env, mode="image")
    errors = [f"{d.code}: {d.message}" for d in result.diagnostics if d.severity == "error"]
    warnings = [f"{d.code}: {d.message}" for d in result.diagnostics if d.severity != "error"]
    return errors, warnings


def env_check(manifest_path: Path, env: Mapping[str, str]) -> int:
    errors, warnings = env_problems(manifest_path, env)
    for line in [*errors, *warnings]:
        print(line, file=sys.stderr)
    return 1 if errors else 0


def check_plan(lock_path: Path) -> int:
    """Spawn every venv worker and init every step; non-zero on any import failure (Dockerfile smoke test)."""
    from wynd.runtime.errors import StepDefinitionError
    from wynd.runtime.worker.client import WorkerCrashed, WorkerRpcError
    from wynd.runtime.worker.pool import WorkerPool
    from wynd.spec.lockfiles import load_process_lock

    plan = load_process_lock(lock_path).plan
    failures = 0
    with WorkerPool(plan) as pool:
        pool.start()
        for step_id in plan.steps:
            try:
                pool.describe(step_id)
            except (StepDefinitionError, WorkerCrashed, WorkerRpcError) as err:
                failures += 1
                print(f"FAIL {step_id}: {err}", file=sys.stderr)
    if failures:
        print(f"{failures} of {len(plan.steps)} steps failed to load", file=sys.stderr)
        return 1
    print(f"ok: {len(plan.steps)} steps in {len(plan.venvs)} venvs")
    return 0


def serve(plan_path: Path, *, host: str | None, port: int | None, env: Mapping[str, str],
          stop: threading.Event | None = None) -> int:
    """Boot (`$DRAFTS/03 §13.2`): HTTP first (healthz answers while workers warm), then warm workers, then ready iff
    the env check passes. SIGTERM/SIGINT (or `stop`): draining -> wait for runs -> stop workers -> stop HTTP."""
    from wynd.runtime.executor import Executor
    from wynd.runtime.storage import stores_from_env
    from wynd.runtime.supervisor.http import make_server
    from wynd.runtime.supervisor.runs import RunManager
    from wynd.runtime.worker.pool import WorkerPool
    from wynd.spec.lockfiles import load_process_lock

    plan = load_process_lock(plan_path).plan
    data_dir = env.get("WYND_DATA_DIR") or DEFAULT_DATA_DIR
    stores = stores_from_env(env, data_dir=data_dir)
    pool = WorkerPool(plan, env=env)
    edge_checker = None
    if plan.edge_venvs:
        from wynd.runtime.edges import WorkerEdgeChecker

        edge_checker = WorkerEdgeChecker(pool, plan)
    executor = Executor(plan, pool, stores, env=env, edge_checker=edge_checker)
    manager = RunManager(
        executor, stores, uploads_dir=Path(data_dir) / "uploads",
        max_concurrent=_int(env, "WYND_MAX_CONCURRENT_RUNS", 4), max_queued=_int(env, "WYND_MAX_QUEUED_RUNS", 64),
        retention=_int(env, "WYND_RUN_RETENTION", 1000), max_body_mb=_int(env, "WYND_MAX_BODY_MB", 32),
        workers=pool.status,
    )
    server = make_server(manager, host=host or env.get("WYND_HOST") or "0.0.0.0",
                         port=port if port is not None else _int(env, "WYND_PORT", 8080),
                         token=env.get("WYND_RUN_API_TOKEN") or None)
    stop = stop or threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
    http_thread = threading.Thread(target=server.serve_forever, name="wynd-http", daemon=True)
    http_thread.start()
    bound_host, bound_port = server.server_address[:2]
    print(f"wynd-supervisor: listening on http://{bound_host}:{bound_port}", file=sys.stderr, flush=True)

    pool.start()
    errors, warnings = env_problems(Path(env.get("WYND_ENV_MANIFEST") or DEFAULT_MANIFEST), env)
    for line in warnings:
        log.warning("env: %s", line)
    if errors:
        manager.problems = errors
        for line in errors:
            log.error("env check failed, not becoming ready: %s", line)
    else:
        manager.mark_ready()
        log.info("ready: process %s, %d venvs", plan.root, len(plan.venvs))

    stop.wait()
    log.info("draining")
    drained = manager.drain(_float(env, "WYND_DRAIN_TIMEOUT_S", 30.0))
    server.shutdown()
    server.server_close()
    if not drained:
        # in-flight runs would block the worker shutdown and interpreter exit; the process is going away anyway
        log.warning("drain timeout: exiting with runs still in flight")
        logging.shutdown()
        os._exit(1)
    pool.close()
    return 0


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    return int(env.get(name) or default)


def _float(env: Mapping[str, str], name: str, default: float) -> float:
    return float(env.get(name) or default)

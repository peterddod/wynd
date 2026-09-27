"""Process routes: list/create/get, design, builds, interface, status, validate, test, history, files, env
(PLAN §3.21 incl. amendment 14; `$DRAFTS/06 §8.3` routes 3-7, 13, 14 and the (+) process routes).

Suffixed `{process_id:path}` routes are registered before `GET /api/processes/{process_id:path}`. `flags` is a
comma-separated subset of design, compiled, built, released. The env report (`GET …/env`, `$DRAFTS/06 §10.4`
`EnvCheckReport`) lists every var of the process's manifest with whether it is set and where its value would come
from: `env` (the controller's environment), `dotenv` (the workspace `.env`), `registry` (user-registry secrets),
`default`, else `missing` (required) or `unset`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import get_args

from fastapi import APIRouter

from wynd.controller.api.app import Ctl
from wynd.controller.api.models_web import CreateProcessRequest, SaveRequest
from wynd.controller.controller import Controller
from wynd.controller.errors import Invalid
from wynd.controller.models import EnvCheckReport, EnvCheckRow, EnvVar, StatusFlag

PROCESS = "/api/processes/{process_id:path}"
FLAGS = get_args(StatusFlag)

router = APIRouter()


@router.get("/api/processes")
def list_processes(ctl: Ctl, q: str = "", flags: str = ""):
    return {"processes": ctl.processes.list(q, _flags(flags))}


@router.post("/api/processes", status_code=201)
def create_process(body: CreateProcessRequest, ctl: Ctl):
    return ctl.processes.new(body.id, goal=body.goal, root=body.root)


@router.post("/api/workspace/validate")
def validate_workspace(ctl: Ctl):
    return {"reports": ctl.processes.validate_all()}


@router.get(f"{PROCESS}/design")
def get_design(process_id: str, ctl: Ctl):
    return ctl.design.get(process_id)


@router.post(f"{PROCESS}/design")
def save_design(process_id: str, body: SaveRequest, ctl: Ctl):
    return ctl.design.save(process_id, body, origin="web")


@router.get(f"{PROCESS}/builds")
def list_builds(process_id: str, ctl: Ctl):
    return {"builds": ctl.processes.builds(process_id)}


@router.get(f"{PROCESS}/interface")
def get_interface(process_id: str, ctl: Ctl, commit: str | None = None):
    return ctl.processes.interface(process_id, commit or None)


@router.get(f"{PROCESS}/status")
def get_status(process_id: str, ctl: Ctl):
    return ctl.processes.status(process_id)


@router.post(f"{PROCESS}/validate")
def validate_process(process_id: str, ctl: Ctl):
    return ctl.processes.validate(process_id)


@router.post(f"{PROCESS}/test")
def test_process(process_id: str, ctl: Ctl):
    return ctl.processes.test(process_id)


@router.get(f"{PROCESS}/history")
def history(process_id: str, ctl: Ctl, limit: int = 50):
    return {"commits": ctl.processes.history(process_id, limit)}


@router.get(f"{PROCESS}/files")
def read_file(process_id: str, path: str, ctl: Ctl):
    return ctl.processes.read_file(process_id, path)


@router.get(f"{PROCESS}/env")
def env_report(process_id: str, ctl: Ctl):
    return _env_report(ctl, process_id)


@router.get(PROCESS)
def get_process(process_id: str, ctl: Ctl):
    return ctl.processes.get(process_id)


def _flags(text: str) -> list[str]:
    flags = [flag.strip() for flag in text.split(",") if flag.strip()]
    unknown = [flag for flag in flags if flag not in FLAGS]
    if unknown:
        raise Invalid(f"unknown status flag(s) {', '.join(unknown)}; flags are {', '.join(FLAGS)}")
    return flags


def _env_report(ctl: Controller, pid: str) -> EnvCheckReport:
    from wynd.controller.envfile import ENV_FILE, read_env_file

    manifest = ctl.env.manifest(pid)
    check = ctl.env.check(pid, mode="local")
    resolved = ctl.env.resolve()
    dotenv = read_env_file(ctl.ctx.root / ENV_FILE)
    environ = {name: value for name, value in ctl.ctx.env.items() if dotenv.get(name) != value}   # .env was loaded
    layers = [("env", environ), ("dotenv", dotenv), ("registry", ctl.ctx.stores.registry.secrets())]
    rows = [EnvCheckRow(name=var.name, required=var.required, secret=var.secret, description=var.description,
                        used_by=list(var.used_by), set=bool(resolved.get(var.name)), source=_source(var, layers))
            for var in manifest.vars]
    return EnvCheckReport(process=pid, manifest="build" if manifest.commit else "assembled", commit=manifest.commit,
                          ok=check.ok, vars=rows)


def _source(var: EnvVar, layers: list[tuple[str, Mapping[str, str]]]) -> str:
    for label, values in layers:
        if values.get(var.name):
            return label
    if var.default is not None:
        return "default"
    return "missing" if var.required and var.one_of is None else "unset"

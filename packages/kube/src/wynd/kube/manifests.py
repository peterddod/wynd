"""Manifest renderers (`$DRAFTS/08 §5.5, §5.7, §5.8, §5.10`, PLAN §11): job Jobs, trigger CronJobs, release
ConfigMap + Deployment + Service, the per-process Secret template and the cluster install. Objects are plain dicts
built in canonical key order (apiVersion, kind, metadata, spec); goldens live in `packages/kube/tests/golden/`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from wynd.controller.api.models_web import Release
    from wynd.kube.config import InstallParams, KubeConfig
    from wynd.spec.env_manifest import EnvManifest


def render_job(cfg: KubeConfig, *, job_id: str, kind: Literal["compile", "test_live", "build", "optimise"],
               ref: str, process: str) -> dict[str, Any]:
    """The batch/v1 Job for one job attempt. Main container runs `python -m wynd.controller.jobs.worker --workspace
    /work/repo/<subdir> --job <id> --checkout clone --remote <git_url>`; build jobs run the `prepare` phase, the
    rootless BuildKit init container and the `finalize` phase over a shared emptyDir at $WYND_BUILD_CONTEXT_DIR
    (PLAN §11 item 2)."""
    raise NotImplementedError("PLAN §11")


def render_cronjob(cfg: KubeConfig, release: Release) -> dict[str, Any]:
    """The CronJob of a schedule-triggered release; its pod runs `wynd-kube fire <release id>`."""
    raise NotImplementedError("PLAN §11")


def render_process_release(cfg: KubeConfig, release: Release, manifest: EnvManifest,
                           env: Mapping[str, str]) -> list[dict[str, Any]]:
    """[ConfigMap, Deployment, Service] serving the release image with the supervisor on cfg.serving_port and
    `wynd-supervisor env-check` as init container; non-secret values from env (PLAN §11 item 5)."""
    raise NotImplementedError("PLAN §11")


def render_env_template(manifest: EnvManifest, *, namespace: str) -> str:
    """YAML text of the per-process Secret skeleton (empty values), with one comment line per secret var."""
    raise NotImplementedError("PLAN §11")


def render_install(p: InstallParams) -> list[dict[str, Any]]:
    """Namespace (optional), ServiceAccounts, Role, RoleBinding, state PVC, ConfigMap wynd-config, controller
    Deployment, Services wynd-controller and wynd-webhooks, in that order."""
    raise NotImplementedError("PLAN §11")


def to_yaml(objs: dict[str, Any] | list[dict[str, Any]]) -> str:
    """yaml.safe_dump(obj, sort_keys=False, default_flow_style=False); a list is joined as a `---\\n` stream."""
    raise NotImplementedError("PLAN §11")

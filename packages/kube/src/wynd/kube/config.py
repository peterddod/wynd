"""Kube configuration (`$DRAFTS/08 §5.2`, PLAN §11): `KubeConfig` read from `WYND_KUBE_*` env vars by the backend
factories, and `InstallParams` for `render_install`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class KubeConfig:
    namespace: str                                       # WYND_KUBE_NAMESPACE (required)
    toolchain_image: str                                 # WYND_KUBE_TOOLCHAIN_IMAGE (required): the wynd image
    git_url: str                                         # WYND_KUBE_GIT_URL (required): https clone URL of the workspace repo
    workspace_subdir: str = "."                          # WYND_KUBE_WORKSPACE_SUBDIR
    buildkit_image: str = "moby/buildkit:v0.27.1-rootless"   # WYND_KUBE_BUILDKIT_IMAGE
    state_pvc: str = "wynd-state"                        # WYND_KUBE_STATE_PVC
    state_dir: str = "/var/lib/wynd"                     # mount path of the state PVC in wynd containers
    config_map: str = "wynd-config"                      # WYND_KUBE_CONFIGMAP
    git_secret: str = "wynd-git"                         # WYND_KUBE_GIT_SECRET (key: .git-credentials)
    model_secret: str = "wynd-model-credentials"         # WYND_KUBE_MODEL_SECRET (CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY)
    registry_secret: str = "wynd-registry"               # WYND_KUBE_REGISTRY_SECRET (type kubernetes.io/dockerconfigjson)
    api_token_secret: str = "wynd-api-token"             # WYND_KUBE_API_TOKEN_SECRET (key: WYND_API_TOKEN)
    registry_insecure: bool = False                      # WYND_KUBE_REGISTRY_INSECURE=1 (kind/local registries only)
    job_ttl_s: int = 86400                               # WYND_KUBE_JOB_TTL_S
    job_deadline_s: int = 3600                           # WYND_KUBE_JOB_DEADLINE_S
    controller_url: str = "http://wynd-controller:8780"  # WYND_KUBE_CONTROLLER_URL (port 8780, PLAN §11 item 3)
    serving_port: int = 8080

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> KubeConfig:
        """A missing required var raises ValueError naming the var."""
        raise NotImplementedError("PLAN §11")


@dataclass(frozen=True)
class InstallParams:
    namespace: str = "wynd"
    image: str = "ghcr.io/OWNER/wynd:0.1.0"
    git_url: str = "https://github.com/OWNER/REPO.git"
    git_branch: str = "main"
    workspace_subdir: str = "."
    storage_class: str | None = None                     # None -> cluster default
    storage_size: str = "20Gi"
    storage_access_mode: Literal["ReadWriteMany", "ReadWriteOnce"] = "ReadWriteMany"
    buildkit_image: str = "moby/buildkit:v0.27.1-rootless"
    create_namespace: bool = True
    registry_insecure: bool = False

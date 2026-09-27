"""`render_dockerfile(lock)` goldens rendered from fixed locks (PLAN §6.5; `$DRAFTS/04 §8.5`).

Inputs: `golden/dogfood/process.lock.yaml` (the dogfood at a fixed commit) and `golden/gated/process.lock.yaml` (also
the build-job golden). `WYND_UPDATE_GOLDEN=1` rewrites the Dockerfile goldens.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from wynd.process.build.dockerfile import manifest_label, render_dockerfile
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.fragments import EnvVar
from wynd.spec.lockfiles import BaseChoice, load_process_lock

GOLDEN = Path(__file__).parent / "golden"


def golden(name: str, text: str) -> None:
    path = GOLDEN / name
    if os.environ.get("WYND_UPDATE_GOLDEN") == "1":
        path.write_text(text)
    assert text == path.read_text(), f"golden {name} differs (WYND_UPDATE_GOLDEN=1 rewrites it)"


def test_dogfood_slim():
    lock = load_process_lock(GOLDEN / "dogfood" / "process.lock.yaml")
    golden("Dockerfile.dogfood-slim", render_dockerfile(lock))


def test_gated_has_a_venv_without_wheels_for_its_agentic_branch():
    lock = load_process_lock(GOLDEN / "gated" / "process.lock.yaml")
    text = render_dockerfile(lock)
    assert text == (GOLDEN / "gated" / "Dockerfile").read_text()
    edge_venv = lock.plan.edge_venvs["gated:read.done[keep]"]
    block = text.split(f"uv venv --python /usr/local/bin/python3.12 /opt/wynd/venvs/{edge_venv}")[1]
    assert block.split("uv venv")[0].count("uv pip install") == 1                # requirements only, no wheels


def system_lock(variant: str):
    lock = load_process_lock(GOLDEN / "gated" / "process.lock.yaml")
    base = BaseChoice(requested="alpine-python" if variant == "alpine" else "debian-slim-python", variant=variant,
                      version="0.1.0", image=f"wynd-base:0.1.0-{variant}")
    return lock.model_copy(update={"base": base, "system_packages": ["poppler-utils", "curl"]})


def test_system_packages_slim():
    golden("Dockerfile.system-slim", render_dockerfile(system_lock("slim")))


def test_system_packages_alpine():
    golden("Dockerfile.system-alpine", render_dockerfile(system_lock("alpine")))


def test_output_is_independent_of_venv_and_wheel_order():
    lock = load_process_lock(GOLDEN / "dogfood" / "process.lock.yaml")
    shuffled = lock.model_copy(update={"venvs": list(reversed(lock.venvs)),
                                       "wheels": dict(reversed(list(lock.wheels.items()))),
                                       "system_packages": []})
    assert render_dockerfile(shuffled) == render_dockerfile(lock)


def test_the_manifest_label_is_compact_json_with_dockerfile_escapes():
    manifest = EnvManifest(process="p", commit="abc", vars=[
        EnvVar(name="TOKEN", description='A "quoted" $HOME value\\n', secret=True, used_by=["step:p#a"])])
    line = manifest_label(manifest)
    assert line.startswith('LABEL dev.wynd.env-manifest="') and line.endswith('"\n') and line.count("\n") == 1
    value = line[len('LABEL dev.wynd.env-manifest="'):-2]
    unescaped = value.replace("\\$", "$").replace('\\"', '"').replace("\\\\", "\\")
    assert json.loads(unescaped) == manifest.model_dump(mode="json", exclude_none=True)
    assert "$HOME" not in value.replace("\\$HOME", "")

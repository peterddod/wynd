"""One claude-code agentic call inside a served image (CTL-M2; PLAN §8.2, §14 M2-INT token-conditional part).
Opt-in: `WYND_LIVE=1 WYND_DOCKER=1`; needs a container credential (`CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`)
from the environment or `examples/invoices/.env`, because the host's Keychain login does not reach a container.

The dogfood (provider claude-code) is built from a temp git copy, served with the credential, and example 1 runs with
`--image`: exit `done` through `read, extract, validate, save`, with a claude-code `model.call` in the trace.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from support.ctl_m2_variant import INVOICES, PID, PROCESS_REL, build_image, dogfood_copy
from wynd.controller.controller import Controller
from wynd.controller.envfile import read_env_file
from wynd.controller.models import ImageTarget

pytestmark = [pytest.mark.live, pytest.mark.docker]

CREDENTIALS = ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY")
NO_CREDENTIAL = "no claude-code credential for containers (set CLAUDE_CODE_OAUTH_TOKEN; see docs/providers.md)"
CONTAINER_OUT = "/tmp/wynd-out"


def container_credential() -> dict[str, str]:
    dotenv = read_env_file(INVOICES / ".env")
    for name in CREDENTIALS:
        value = os.environ.get(name) or dotenv.get(name)
        if value:
            return {name: value}
    return {}


def test_claude_code_call_inside_a_served_image(tmp_path, shared_venvs, monkeypatch):
    credential = container_credential()
    if not credential:
        pytest.skip(NO_CREDENTIAL)
    monkeypatch.delenv("UV_OFFLINE", raising=False)
    ws = dogfood_copy(tmp_path, shared_venvs, provider="claude-code")
    env = {name: f"{CONTAINER_OUT}/{name.lower()}" for name in ("RECORDS_DIR", "REVIEW_DIR", "ESCALATIONS_DIR")}
    ctl = Controller.open(ws, environ={**os.environ, **env, **credential}, load_dotenv=False)

    image = build_image(ctl, PID)
    try:
        ctl.serve.serve(image, timeout=180)
        pdf = Path(ws / PROCESS_REL / "examples" / "acme_inv_1042.pdf")
        run = ctl.runs.run(PID, {"pdf_path": str(pdf)}, target=ImageTarget())

        assert run.exit == "done", run.error
        events = ctl.runs.events(run.id)
        assert [e["step"] for e in events if e["type"] == "step.end"] == ["read", "extract", "validate", "save"]
        calls = [e for e in events if e["type"] == "model.call"]
        assert calls and all(c["provider"] == "claude-code" and c["cassette"] == "live" for c in calls)
    finally:
        ctl.serve.stop(image)
        subprocess.run(["docker", "image", "rm", "-f", image], capture_output=True, check=False)

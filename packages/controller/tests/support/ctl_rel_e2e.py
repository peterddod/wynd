"""CTL-REL helper for the release cases of the Docker end-to-end test (PLAN §8.2: "release manual + webhook are added
in M4-INT"), also exercised offline with the fakes by `test_releases.py`.

- `fire_manual_and_webhook(ctl, pid, commit, inputs, *, env, secret_env)`: creates a manual and a webhook release of
  the build of `pid` at `commit` with the `env` bindings, fires the first as `POST /api/releases/{id}/trigger` does
  and the second as `POST /hooks/releases/{id}` does (a wrong secret is refused, the controller's value of
  `secret_env` is accepted), waits for both runs and deletes both releases (which removes their containers).
  -> `(manual run, webhook run)` as finished `Run` DTOs.
- `wait_for_run(ctl, run_id, timeout)`: polls `ctl.runs.get` until the run is `succeeded` or `failed`.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pytest

RUN_TIMEOUT = 180.0


def wait_for_run(ctl, run_id: str, timeout: float = RUN_TIMEOUT):
    deadline = time.monotonic() + timeout
    while True:
        run = ctl.runs.get(run_id)
        if run.status in ("succeeded", "failed"):
            return run
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} still {run.status} after {timeout:g}s")
        time.sleep(0.1)


def fire_manual_and_webhook(
    ctl, pid: str, commit: str, inputs: dict[str, Any], *, env: Mapping[str, Any], secret_env: str,
    timeout: float = RUN_TIMEOUT,
):
    from wynd.controller.api.models_web import CreateReleaseRequest, ManualTrigger, WebhookTrigger
    from wynd.controller.errors import Unauthorized

    secret = ctl.env.resolve()[secret_env]
    manual = ctl.releases.create(CreateReleaseRequest(process_id=pid, commit=commit, trigger=ManualTrigger(),
                                                      env=dict(env)))
    hook = ctl.releases.create(CreateReleaseRequest(process_id=pid, commit=commit,
                                                    trigger=WebhookTrigger(secret_env=secret_env), env=dict(env)))
    try:
        started = ctl.releases.trigger(manual.id, inputs, source="manual")
        manual_run = wait_for_run(ctl, started.id, timeout)

        with pytest.raises(Unauthorized):
            ctl.releases.verify_webhook(hook.id, secret + "-wrong")
        ctl.releases.verify_webhook(hook.id, secret)
        started = ctl.releases.trigger(hook.id, inputs, source="webhook")
        hook_run = wait_for_run(ctl, started.id, timeout)
    finally:
        for release in (manual, hook):
            ctl.releases.delete(release.id)
    return manual_run, hook_run

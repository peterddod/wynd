"""`fire`: what a trigger CronJob runs (`$DRAFTS/08 §5.8`, PLAN §11 item 4). Firing through the controller keeps run
records, trace persistence and usage accounting in one place."""

from __future__ import annotations


def fire(release_id: str, controller_url: str, token: str | None, *, timeout_s: float = 30.0) -> str:
    """POST {controller_url}/api/releases/{release_id}/trigger with body {"source": "schedule"} and
    `Authorization: Bearer <token>` when set; returns the id of the returned Run. urllib (stdlib), one attempt;
    non-2xx -> SystemExit(1) with the response body on stderr."""
    raise NotImplementedError("PLAN §11")

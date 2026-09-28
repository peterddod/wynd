"""Release and trigger-fire documents in the controller `DocStore` (PLAN §8.1 releases row, §3.24;
`$DRAFTS/06 §10.2`).

Stored at `.wynd/controller/releases/<id>.json` and `.wynd/controller/fires/<id>.json`. A release document holds
`id, process_id, commit, image, trigger, env, enabled, serving, triggers, created_at, updated_at, state` (the last
known serving state, read by `status.py`) and the scheduler's `last_fire_key`/`last_fired_at`. Fire documents are
`TriggerFire` JSON: `at` is when the trigger was called, `started_at` when the run was submitted and `finished_at`
when its mirror ended (SPEC §15).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from wynd.controller.errors import NotFound

if TYPE_CHECKING:
    from wynd.controller.api.models_web import Release
    from wynd.controller.store import DocStore

RELEASES = "releases"
FIRES = "fires"


def release_doc(docs: DocStore, release_id: str) -> dict[str, Any]:
    doc = docs.get(RELEASES, release_id) if _valid(release_id) else None
    if doc is None:
        raise NotFound(f"unknown release '{release_id}'")
    return doc


def to_release(doc: dict[str, Any]) -> Release:
    """The `Release` DTO of a stored document with what needs no git, clock or serving call: `behind` 0,
    `state` as last recorded, no `next_fire_at` (`ReleaseService` fills those in)."""
    from wynd.controller.api.models_web import Release

    trigger = doc["trigger"]
    return Release(
        id=doc["id"], process_id=doc["process_id"], commit=doc["commit"], short=doc["commit"][:7],
        image=doc["image"], trigger=trigger, env=doc.get("env") or {}, enabled=doc["enabled"],
        state=doc.get("state") or "stopped", created_at=doc["created_at"],
        webhook_url=webhook_path(doc["id"]) if trigger["kind"] == "webhook" else None,
    )


def webhook_path(release_id: str) -> str:
    return f"/hooks/releases/{release_id}"


def _valid(release_id: str) -> bool:
    from wynd.runtime.ids import valid_id

    return valid_id(release_id)

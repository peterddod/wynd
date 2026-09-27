"""Chat documents in the controller `DocStore` (PLAN §8.1 chat row; `$DRAFTS/06 §7.2`, §10.2).

`.wynd/controller/chats/<id>.json`: `{id, title, created_at, updated_at, job_id, items, next_seq, usage_totals,
client_ids}`. Items are the web `ChatItem` union in JSON form (built through the DTOs, so a stored item always
validates), with ids `it_<seq>`; deltas are never persisted. `client_ids` maps a message's `client_id` to
`{turn_id, item_id}` so a repeated send returns the existing turn.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter

from wynd.controller.api.models_web import ChatItem
from wynd.controller.errors import NotFound
from wynd.runtime.ids import valid_id

if TYPE_CHECKING:
    from pydantic import BaseModel

    from wynd.controller.store import DocStore

CHATS = "chats"
DEFAULT_TITLE = "New chat"
_ITEM = TypeAdapter(ChatItem)
_DATETIME = TypeAdapter(datetime)


def new_record(chat_id: str, title: str, job_id: str | None, now: datetime) -> dict[str, Any]:
    stamp = timestamp(now)
    return {"id": chat_id, "title": title, "created_at": stamp, "updated_at": stamp, "job_id": job_id, "items": [],
            "next_seq": 1, "usage_totals": None, "client_ids": {}}


def load(docs: DocStore, chat_id: str) -> dict[str, Any]:
    """`NotFound` for an unknown (or malformed) id."""
    record = docs.get(CHATS, chat_id) if valid_id(chat_id) else None
    if record is None:
        raise NotFound(f"no chat '{chat_id}'")
    return record


def save(docs: DocStore, record: dict[str, Any]) -> None:
    docs.put(CHATS, record["id"], record)


def list_records(docs: DocStore) -> list[dict[str, Any]]:
    return docs.list(CHATS)


def delete(docs: DocStore, chat_id: str) -> None:
    docs.delete(CHATS, chat_id)


def append_item(record: dict[str, Any], model: type[BaseModel], now: datetime, **fields: Any) -> dict[str, Any]:
    """Add an item of DTO class `model` with the next `seq`; -> the stored JSON item (also bumps `updated_at`)."""
    seq = record["next_seq"]
    item = model(id=f"it_{seq}", seq=seq, created_at=now, **fields).model_dump(mode="json")
    record["items"].append(item)
    record["next_seq"] = seq + 1
    record["updated_at"] = timestamp(now)
    return item


def find_item(record: dict[str, Any], item_id: str) -> dict[str, Any]:
    return next(item for item in record["items"] if item["id"] == item_id)


def to_item(item: dict[str, Any]) -> ChatItem:
    return _ITEM.validate_python(item)


def timestamp(now: datetime) -> str:
    """ISO-8601 UTC with `Z`, as the DTOs serialise datetimes."""
    return _DATETIME.dump_python(now, mode="json")


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text)

"""`FileDocStore` (PLAN §8.1 store row; `$DRAFTS/06 §10.2`)."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from datetime import UTC, datetime

import pytest

from wynd.controller.store import FileDocStore


@pytest.fixture
def store(tmp_path) -> FileDocStore:
    return FileDocStore(tmp_path / "controller")


def test_put_get_round_trip_one_file_per_document(store, tmp_path):
    doc = {"id": "rel_1", "process_id": "p1", "at": datetime(2026, 9, 22, 11, 0, tzinfo=UTC), "nested": {"a": [1]}}
    store.put("releases", "rel_1", doc)
    assert store.get("releases", "rel_1") == {"id": "rel_1", "process_id": "p1", "at": "2026-09-22T11:00:00Z",
                                              "nested": {"a": [1]}}
    path = tmp_path / "controller" / "releases" / "rel_1.json"
    assert json.loads(path.read_text())["process_id"] == "p1"
    assert [p.name for p in path.parent.iterdir()] == ["rel_1.json"]         # no temp files left behind
    assert store.get("releases", "rel_2") is None
    assert store.get("fires", "rel_1") is None


def test_put_replaces_the_whole_document(store):
    store.put("chats", "chat_1", {"id": "chat_1", "title": "a", "items": [1]})
    store.put("chats", "chat_1", {"id": "chat_1", "title": "b"})
    assert store.get("chats", "chat_1") == {"id": "chat_1", "title": "b"}


def test_update_is_a_shallow_merge_and_needs_the_document(store):
    store.put("releases", "rel_1", {"id": "rel_1", "enabled": True, "trigger": {"kind": "manual"}})
    merged = store.update("releases", "rel_1", {"enabled": False, "trigger": {"kind": "webhook"}})
    assert merged == {"id": "rel_1", "enabled": False, "trigger": {"kind": "webhook"}}
    assert store.get("releases", "rel_1") == merged
    with pytest.raises(KeyError):
        store.update("releases", "rel_404", {"enabled": True})


def test_list_filters_with_where_newest_id_first_and_limits(store):
    for n, process in enumerate(["p1", "p2", "p1", "p1"]):
        store.put("releases", f"rel_2026092{n}", {"id": f"rel_2026092{n}", "process_id": process})
    assert [d["id"] for d in store.list("releases")] == ["rel_20260923", "rel_20260922", "rel_20260921",
                                                         "rel_20260920"]
    assert [d["id"] for d in store.list("releases", where={"process_id": "p1"})] == [
        "rel_20260923", "rel_20260922", "rel_20260920"]
    assert [d["id"] for d in store.list("releases", where={"process_id": "p1"}, limit=2)] == [
        "rel_20260923", "rel_20260922"]
    assert store.list("releases", where={"process_id": "p3"}) == []
    assert store.list("fires") == []                                          # a collection nobody wrote


def test_delete_is_idempotent(store):
    store.put("fires", "fire_1", {"id": "fire_1"})
    store.delete("fires", "fire_1")
    store.delete("fires", "fire_1")
    assert store.get("fires", "fire_1") is None


@pytest.mark.parametrize(("collection", "id"), [("Releases", "rel_1"), ("releases", "../x"), ("releases", ""),
                                                ("a/b", "x"), ("releases", ".hidden")])
def test_invalid_names_are_refused(store, collection, id):
    with pytest.raises(ValueError, match="invalid"):
        store.put(collection, id, {})
    with pytest.raises(ValueError, match="invalid"):
        store.get(collection, id)


def test_concurrent_updates_from_threads_lose_nothing(store):
    store.put("chats", "chat_1", {"id": "chat_1"})

    def worker(n: int) -> None:
        for i in range(20):
            store.update("chats", "chat_1", {f"t{n}_{i}": i})

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.get("chats", "chat_1")) == 1 + 4 * 20


def test_concurrent_updates_from_two_processes_lose_nothing(store, tmp_path):
    store.put("chats", "chat_1", {"id": "chat_1"})
    script = (
        "import sys\n"
        "from wynd.controller.store import FileDocStore\n"
        "s = FileDocStore(sys.argv[1])\n"
        "for i in range(25):\n"
        "    s.update('chats', 'chat_1', {f'{sys.argv[2]}_{i}': i})\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", script, str(store.root), tag]) for tag in ("a", "b")]
    assert [p.wait(timeout=60) for p in procs] == [0, 0]
    assert len(store.get("chats", "chat_1")) == 1 + 2 * 25

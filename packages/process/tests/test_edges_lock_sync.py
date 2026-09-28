"""`sync_edge_lock` (PLAN §3.7; `$DRAFTS/08 §4.1`, §4.8): adds default entries for new agentic branches, refreshes
`check_hash` for edited checks and drops entries whose branch is gone, preserving the knobs of surviving entries;
returns `(lock, changed)` and never writes."""

from pathlib import Path

import pytest

from wynd.process.edges_lock import agentic_branches, sync_edge_lock
from wynd.spec.errors import SpecError
from wynd.spec.lockfiles import EdgeLockEntry, EdgesLock, check_hash, dump_lock, load_edges_lock
from wynd.spec.process_doc import ProcessDoc
from wynd.spec.workspace import EDGES_LOCK_FILE
from wynd.spec.yamlio import parse_model

SAVE = "The document is a final supplier invoice."
RECEIPT = "The document is a receipt."

PROCESS = """kind: process
name: main
entry: read
inputs:
  value: string
outputs:
  value: string
steps:
  read:  {{ use: ./steps/read }}
  write: {{ use: ./steps/write }}
  other: {{ use: ./steps/other }}
edges:
{edges}"""

AGENTIC = """  - from: read.done
    kind: agentic
    to:
      - step: write
        name: save
        check: The document is a final supplier invoice.
        with: { value: steps.read.outputs.value }
      - step: write
        check: The document is a receipt.
        context: [steps.read.outputs]
        with: { value: steps.read.outputs.value }
      - step: other
        with: { value: steps.read.outputs.value }
"""

TAIL = """  - from: write.done
    to: $exit.done
    with: { value: steps.write.outputs.value }
  - from: other.done
    to: $exit.done
    with: { value: steps.other.outputs.value }
"""


def doc(edges: str = AGENTIC) -> ProcessDoc:
    return parse_model(PROCESS.format(edges=edges + TAIL), ProcessDoc, "processes/main/process.yaml")


def write_lock(process_dir: Path, lock: EdgesLock) -> Path:
    path = process_dir / EDGES_LOCK_FILE
    path.write_text(dump_lock(lock), encoding="utf-8")
    return path


FRESH = EdgesLock(edges={
    "read.done[1]": EdgeLockEntry(check_hash=check_hash(RECEIPT, ["steps.read.outputs"])),
    "read.done[save]": EdgeLockEntry(check_hash=check_hash(SAVE, None)),
})


def test_new_agentic_branches_get_default_entries_and_nothing_is_written(tmp_path):
    lock, changed = sync_edge_lock(tmp_path, doc())
    assert changed
    assert lock == FRESH
    assert list(lock.edges) == ["read.done[1]", "read.done[save]"]          # sorted by branch key
    entry = lock.edges["read.done[save]"]
    knobs = (entry.provider, entry.tier, entry.thinking, entry.retries, entry.timeout_s)
    assert knobs == (None, "cheap", "low", 2, 120)
    assert not (tmp_path / EDGES_LOCK_FILE).exists()


def test_a_process_without_agentic_edges_needs_no_lock(tmp_path):
    deterministic = AGENTIC.replace("    kind: agentic\n", "")
    assert sync_edge_lock(tmp_path, doc(deterministic)) == (EdgesLock(), False)


def test_an_up_to_date_lock_is_unchanged(tmp_path):
    path = write_lock(tmp_path, FRESH)
    before = path.read_bytes()
    lock, changed = sync_edge_lock(tmp_path, doc())
    assert (lock, changed) == (FRESH, False)
    assert path.read_bytes() == before


def test_the_dumped_lock_round_trips_and_syncs_unchanged(tmp_path):
    lock, _ = sync_edge_lock(tmp_path, doc())
    path = write_lock(tmp_path, lock)
    assert load_edges_lock(path) == lock
    assert sync_edge_lock(tmp_path, doc()) == (lock, False)


def test_edited_check_refreshes_the_hash_and_keeps_the_knobs(tmp_path):
    tuned = EdgeLockEntry(check_hash=check_hash("The document is an invoice.", None), provider="anthropic",
                          tier="standard", thinking="medium", retries=4, timeout_s=30)
    write_lock(tmp_path, EdgesLock(edges={**FRESH.edges, "read.done[save]": tuned}))
    lock, changed = sync_edge_lock(tmp_path, doc())
    assert changed
    assert lock.edges["read.done[save]"] == tuned.model_copy(update={"check_hash": check_hash(SAVE, None)})
    assert lock.edges["read.done[1]"] == FRESH.edges["read.done[1]"]


def test_edited_context_refreshes_the_hash(tmp_path):
    stale = EdgeLockEntry(check_hash=check_hash(RECEIPT, None), tier="strong")        # locked before `context:`
    write_lock(tmp_path, EdgesLock(edges={**FRESH.edges, "read.done[1]": stale}))
    lock, changed = sync_edge_lock(tmp_path, doc())
    assert changed
    assert lock.edges["read.done[1]"] == EdgeLockEntry(check_hash=check_hash(RECEIPT, ["steps.read.outputs"]),
                                                       tier="strong")


def test_reflowing_a_check_changes_nothing(tmp_path):
    write_lock(tmp_path, FRESH)
    reflowed = AGENTIC.replace(f"        check: {SAVE}\n",
                               "        check: >-\n          The document is a final\n          supplier   invoice.\n")
    assert sync_edge_lock(tmp_path, doc(reflowed)) == (FRESH, False)


def test_entries_of_removed_branches_are_dropped(tmp_path):
    gone = EdgeLockEntry(check_hash=check_hash("Gone.", None), tier="strong")
    write_lock(tmp_path, EdgesLock(edges={**FRESH.edges, "read.done[gone]": gone, "write.done[0]": gone}))
    lock, changed = sync_edge_lock(tmp_path, doc())
    assert (lock, changed) == (FRESH, True)


def test_renaming_a_branch_moves_it_to_a_fresh_entry(tmp_path):
    tuned = FRESH.edges["read.done[save]"].model_copy(update={"tier": "standard"})
    write_lock(tmp_path, EdgesLock(edges={**FRESH.edges, "read.done[save]": tuned}))
    lock, changed = sync_edge_lock(tmp_path, doc(AGENTIC.replace("name: save", "name: keep")))
    assert changed
    assert lock.edges == {"read.done[1]": FRESH.edges["read.done[1]"],
                          "read.done[keep]": FRESH.edges["read.done[save]"]}


def test_removing_every_agentic_branch_empties_the_lock(tmp_path):
    write_lock(tmp_path, FRESH)
    assert sync_edge_lock(tmp_path, doc(AGENTIC.replace("    kind: agentic\n", ""))) == (EdgesLock(), True)


def test_checks_on_deterministic_edges_and_after_the_else_are_not_locked(tmp_path):
    late = "      - step: write\n        name: late\n        check: Never evaluated.\n"
    edges = AGENTIC + late + """  - from: write.error
    to:
      - step: other
        check: Not an agentic edge.
        with: { value: steps.read.outputs.value }
      - step: $exit.done
        with: { value: steps.read.outputs.value }
"""
    assert [key for _, _, key, _ in agentic_branches(doc(edges))] == ["read.done[save]", "read.done[1]"]
    assert sync_edge_lock(tmp_path, doc(edges)) == (FRESH, True)


def test_every_agentic_edge_of_the_process_is_locked(tmp_path):
    second = """  - from: write.error
    kind: agentic
    to: other
    check: The failure is worth a second try.
    with: { value: steps.read.outputs.value }
"""
    lock, changed = sync_edge_lock(tmp_path, doc(AGENTIC + second))
    assert changed
    assert lock.edges == {**FRESH.edges,
                          "write.error[0]": EdgeLockEntry(check_hash=check_hash("The failure is worth a second try.",
                                                                                None))}
    assert [(i, j, key) for i, j, key, _ in agentic_branches(doc(AGENTIC + second))] == [
        (0, 0, "read.done[save]"), (0, 1, "read.done[1]"), (1, 0, "write.error[0]"),
    ]


def test_an_invalid_existing_lock_is_an_error_not_overwritten(tmp_path):
    path = tmp_path / EDGES_LOCK_FILE
    path.write_text("wynd: 1\nedges: {read.done[save]: {tier: huge}}\n", encoding="utf-8")
    with pytest.raises(SpecError):
        sync_edge_lock(tmp_path, doc())
    assert "huge" in path.read_text()


def test_process_dir_may_be_a_string(tmp_path):
    write_lock(tmp_path, FRESH)
    assert sync_edge_lock(str(tmp_path), doc()) == (FRESH, False)

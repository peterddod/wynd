"""Build artefact store (PLAN §3.24, SPEC §6.4 `.wynd/build/<process>/<commit>/`; `$DRAFTS/04 §11`) and the image
registry entry of the user registry's `registries` section (PLAN §3.14)."""

import importlib.metadata
import json
from datetime import UTC, datetime, timedelta

import pytest

from wynd.process.artefacts import (
    BuildInfo,
    ImageRegistryEntry,
    LocalArtefactStore,
    open_artefact_store,
)
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.fragments import EnvVar

C1, C2 = "1" * 40, "2" * 40
T0 = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)


def info(process="finance/invoices", commit=C1, at=T0, digest=None) -> BuildInfo:
    return BuildInfo(
        process=process, commit=commit, source_sha="f" * 40, job_id="job_1", process_hash="sha256:" + "0" * 64,
        image=f"wynd/finance-invoices:{commit[:12]}", image_id=None, image_digest=digest,
        base={"requested": "debian-slim-python", "variant": "slim", "version": "0.1.0", "image": "wynd-base:0.1.0-slim"},
        manifest=EnvManifest(process=process, commit=commit, vars=[EnvVar(name="RECORDS_DIR")]),
        created_at=at, dir="",
    )


def staged(tmp_path, name, files):
    path = tmp_path / "staging" / name
    path.mkdir(parents=True)
    for rel, text in files.items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text)
    return path


def test_put_moves_the_build_into_place_and_describes_it(tmp_path):
    state = tmp_path / ".wynd"
    store = LocalArtefactStore(state)
    stage = staged(tmp_path, "b1", {"Dockerfile": "FROM x\n", "dist/a.whl": "wheel"})
    stored = store.put_build(stage, info())
    dest = state / "build" / "finance" / "invoices" / C1
    assert stored.dir == str(dest) and not stage.exists()
    assert (dest / "Dockerfile").read_text() == "FROM x\n" and (dest / "dist" / "a.whl").is_file()
    assert json.loads((dest / "build.json").read_text())["dir"] == str(dest)
    assert store.get_build("finance/invoices", C1) == stored
    assert store.local_dir("finance/invoices", C1) == dest


def test_a_build_of_the_same_commit_replaces_the_previous_one(tmp_path):
    store = LocalArtefactStore(tmp_path / ".wynd")
    store.put_build(staged(tmp_path, "old", {"old.txt": "old"}), info(digest="sha256:old"))
    stored = store.put_build(staged(tmp_path, "new", {"new.txt": "new"}), info(digest="sha256:new"))
    dest = store.local_dir("finance/invoices", C1)
    assert sorted(p.name for p in dest.iterdir()) == ["build.json", "new.txt"]
    assert store.get_build("finance/invoices", C1).image_digest == "sha256:new" == stored.image_digest


def test_list_builds_is_newest_first_and_per_process(tmp_path):
    store = LocalArtefactStore(tmp_path / ".wynd")
    store.put_build(staged(tmp_path, "a", {}), info(commit=C1, at=T0))
    store.put_build(staged(tmp_path, "b", {}), info(commit=C2, at=T0 + timedelta(hours=1)))
    store.put_build(staged(tmp_path, "c", {}), info(process="other", commit=C1, at=T0 + timedelta(hours=2)))
    assert [b.commit for b in store.list_builds("finance/invoices")] == [C2, C1]
    assert [b.process for b in store.list_builds("other")] == ["other"]
    assert store.list_builds("finance") == [] and store.list_builds("unknown") == []


def test_missing_builds(tmp_path):
    store = LocalArtefactStore(tmp_path / ".wynd")
    assert store.get_build("finance/invoices", C1) is None
    with pytest.raises(FileNotFoundError, match="no build of process finance/invoices"):
        store.local_dir("finance/invoices", C1)


def test_open_artefact_store_defaults_to_the_local_entry_point(tmp_path):
    names = {ep.name: ep.value for ep in importlib.metadata.entry_points(group="wynd.artefact_stores")}
    assert names == {"local": "wynd.process.artefacts:LocalArtefactStore"}
    for environ in ({}, {"WYND_ARTEFACT_STORE": ""}, {"WYND_ARTEFACT_STORE": "local"}):
        store = open_artefact_store(tmp_path / ".wynd", environ)
        assert isinstance(store, LocalArtefactStore) and store.state_dir == tmp_path / ".wynd"


class FakeEntryPoint:
    def __init__(self, factory):
        self.factory = factory

    def load(self):
        return self.factory


def test_open_artefact_store_selects_another_backend_by_entry_point(tmp_path, monkeypatch):
    class BucketStore:
        def __init__(self, state_dir):
            self.state_dir = state_dir

    real = importlib.metadata.entry_points

    def entry_points(**kw):
        if kw == {"group": "wynd.artefact_stores", "name": "bucket"}:
            return [FakeEntryPoint(BucketStore)]
        return real(**kw)

    monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)
    store = open_artefact_store(tmp_path, {"WYND_ARTEFACT_STORE": "bucket"})
    assert isinstance(store, BucketStore) and store.state_dir == tmp_path
    with pytest.raises(ValueError, match="unknown WYND_ARTEFACT_STORE 'nope'"):
        open_artefact_store(tmp_path, {"WYND_ARTEFACT_STORE": "nope"})


def test_image_registry_entry():
    entry = ImageRegistryEntry(name="local", url="localhost:5001/wynd")
    assert (entry.username_env, entry.password_env, entry.insecure, entry.default) == (None, None, False, False)
    ghcr = ImageRegistryEntry.model_validate({"name": "ghcr", "url": "ghcr.io/peterddod", "username_env": "GH_USER",
                                              "password_env": "GH_TOKEN", "default": True})
    assert ImageRegistryEntry.model_validate_json(ghcr.model_dump_json()) == ghcr

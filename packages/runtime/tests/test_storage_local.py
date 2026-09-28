"""Local storage backends and env-based selection (SPEC §7.1, PLAN §3.14; `$DRAFTS/02 §13` storage cases)."""

import hashlib
import importlib.metadata
import json
import os
import stat
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wynd.runtime.storage import STORAGE_ENV, StorageConfigError, Stores, registry_from_env, stores_from_env
from wynd.runtime.storage import local
from wynd.runtime.storage.local import (
    EnvRegistry,
    FileRegistry,
    FileRunRegistry,
    FileWorkspaceStore,
    JsonlTraceSink,
)
from wynd.runtime.storage.models import RunRecord, TestResult
from wynd.runtime.usage import Usage

READ_ONLY = "registry is read-only (WYND_REGISTRY=env)"


def run_record(id: str, **fields) -> dict:
    return {"id": id, "kind": "run", "process": "p", "status": "queued", **fields}


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def stray_files(root: Path) -> list[str]:
    """Temp files left behind by atomic writes (none may survive)."""
    return [p.name for p in root.rglob("*.tmp")]


# --- FileWorkspaceStore ------------------------------------------------------------------------------------------------


def test_workspace_open_creates_a_local_dir(tmp_path):
    store = FileWorkspaceStore(tmp_path / "workspaces")
    path = store.open("run_1")
    assert path == tmp_path / "workspaces" / "run_1"
    assert path.is_absolute() and path.is_dir()


def test_workspace_duplicate_open_raises(tmp_path):
    store = FileWorkspaceStore(tmp_path)
    store.open("run_1")
    with pytest.raises(FileExistsError):
        store.open("run_1")


def test_workspace_close_keep_returns_uri_and_keeps_files(tmp_path):
    store = FileWorkspaceStore(tmp_path)
    path = store.open("run_1")
    (path / "out.txt").write_text("x")
    uri = store.close("run_1", keep=True)
    assert uri == path.as_uri()
    assert uri.startswith("file://")
    assert (path / "out.txt").read_text() == "x"
    assert store.locate("run_1") == uri


def test_workspace_close_delete_removes_everything(tmp_path):
    store = FileWorkspaceStore(tmp_path)
    path = store.open("run_1")
    (path / ".wynd" / "steps" / "read" / "1").mkdir(parents=True)
    (path / ".wynd" / "steps" / "read" / "1" / "outputs.json").write_text("{}")
    assert store.locate("run_1") == path.as_uri()          # open workspaces are locatable
    assert store.close("run_1", keep=False) is None
    assert not path.exists()
    assert store.locate("run_1") is None


def test_workspace_rejects_unsafe_run_ids(tmp_path):
    store = FileWorkspaceStore(tmp_path / "ws")
    for bad in ("../escape", "a/b", ""):
        with pytest.raises(ValueError, match="invalid run id"):
            store.open(bad)
        assert store.locate(bad) is None
    assert not (tmp_path / "escape").exists()


def test_workspace_relative_root_is_fixed_at_construction(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = FileWorkspaceStore("ws")
    monkeypatch.chdir(tmp_path / "..")                     # steps change cwd; the store must not follow
    assert store.open("run_1") == tmp_path / "ws" / "run_1"


# --- JsonlTraceSink ----------------------------------------------------------------------------------------------------


def event(run_id: str, seq: int, type: str = "step.log", **fields) -> dict:
    return {"v": 1, "seq": seq, "ts": "2026-09-22T21:50:01.100Z", "run_id": run_id, "type": type, **fields}


def test_trace_write_and_read_in_seq_order(tmp_path):
    sink = JsonlTraceSink(tmp_path / "traces")
    sink.write(event("run_a", 2))
    sink.write(event("run_b", 1))
    sink.write(event("run_a", 1, "run.start"))
    sink.write(event("run_a", 3, message="café"))
    got = sink.read("run_a")                               # readable while the handle is still open
    assert [e["seq"] for e in got] == [1, 2, 3]
    assert got[0]["type"] == "run.start"
    assert got[2]["message"] == "café"
    assert [e["seq"] for e in sink.read("run_b")] == [1]
    lines = (tmp_path / "traces" / "run_a.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert lines[2] == json.dumps(event("run_a", 3, message="café"), ensure_ascii=False, separators=(",", ":"))


def test_trace_read_unknown_run_is_empty(tmp_path):
    sink = JsonlTraceSink(tmp_path)
    assert sink.read("run_missing") == []
    assert sink.read("../etc/passwd") == []


def test_trace_skips_a_truncated_last_line(tmp_path):
    sink = JsonlTraceSink(tmp_path)
    sink.write(event("run_a", 1))
    sink.write(event("run_a", 2))
    sink.close("run_a")
    with open(tmp_path / "run_a.jsonl", "a", encoding="utf-8") as f:
        f.write('{"v":1,"seq":3,"ts":"2026-09-22T21:50:0')
    assert [e["seq"] for e in sink.read("run_a")] == [1, 2]


def test_trace_corrupt_middle_line_is_an_error(tmp_path):
    (tmp_path / "run_a.jsonl").write_text('{"seq":1,"run_id":"run_a"}\nnot json\n{"seq":3,"run_id":"run_a"}\n')
    with pytest.raises(ValueError):
        JsonlTraceSink(tmp_path).read("run_a")


def test_trace_uri_is_the_file(tmp_path):
    sink = JsonlTraceSink(tmp_path / "traces")
    sink.write(event("run_a", 1))
    uri = sink.uri("run_a")
    assert uri == (tmp_path / "traces" / "run_a.jsonl").as_uri()
    assert uri.startswith("file://")


def test_trace_write_after_close_appends(tmp_path):
    sink = JsonlTraceSink(tmp_path)
    sink.write(event("run_a", 1))
    sink.close("run_a")
    sink.close("run_a")                                    # idempotent
    sink.write(event("run_a", 2))
    sink.close("run_a")
    assert [e["seq"] for e in sink.read("run_a")] == [1, 2]


def test_trace_converts_non_json_values(tmp_path):
    sink = JsonlTraceSink(tmp_path)
    sink.write(event("run_a", 1, at=datetime(2026, 9, 22, 21, 50, tzinfo=UTC), usage=Usage(calls=1)))
    got = sink.read("run_a")[0]
    assert got["at"] == "2026-09-22T21:50:00Z"
    assert got["usage"]["calls"] == 1


def test_trace_rejects_unsafe_run_id(tmp_path):
    with pytest.raises(ValueError, match="invalid run id"):
        JsonlTraceSink(tmp_path).write(event("../x", 1))


def test_trace_concurrent_writers_keep_whole_lines(tmp_path):
    sink = JsonlTraceSink(tmp_path)
    payload = "y" * 2000

    def writer(k: int) -> None:
        for i in range(250):
            sink.write(event("run_a", k * 1000 + i, data=payload))

    threads = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = sink.read("run_a")
    assert len(got) == 1000
    assert {e["seq"] for e in got} == {k * 1000 + i for k in range(4) for i in range(250)}


# --- FileRunRegistry ---------------------------------------------------------------------------------------------------


def test_registry_create_sets_timestamps_and_get_returns_it(tmp_path):
    runs = FileRunRegistry(tmp_path)
    before = datetime.now(UTC)
    runs.create(run_record("run_1", inputs={"a": 1}))
    got = runs.get("run_1")
    assert got["inputs"] == {"a": 1}
    assert got["updated_at"] == got["created_at"]
    created = datetime.fromisoformat(got["created_at"])
    assert before <= created <= datetime.now(UTC)
    assert (tmp_path / "records" / "run_1.json").is_file()


def test_registry_create_keeps_given_timestamps(tmp_path):
    runs = FileRunRegistry(tmp_path)
    runs.create(run_record("run_1", created_at=datetime(2026, 1, 1, tzinfo=UTC)))
    got = runs.get("run_1")
    assert got["created_at"] == "2026-01-01T00:00:00Z"
    assert got["updated_at"] == "2026-01-01T00:00:00Z"


def test_registry_duplicate_create_raises_and_keeps_the_original(tmp_path):
    runs = FileRunRegistry(tmp_path)
    runs.create(run_record("run_1", status="running"))
    with pytest.raises(FileExistsError):
        runs.create(run_record("run_1", status="queued"))
    assert runs.get("run_1")["status"] == "running"


@pytest.mark.parametrize("missing", ["id", "kind", "status"])
def test_registry_create_needs_id_kind_status(tmp_path, missing):
    record = run_record("run_1")
    del record[missing]
    with pytest.raises(ValueError, match=missing):
        FileRunRegistry(tmp_path).create(record)


def test_registry_create_rejects_unknown_kind_and_unsafe_id(tmp_path):
    runs = FileRunRegistry(tmp_path)
    with pytest.raises(ValueError, match="kind"):
        runs.create(run_record("run_1", kind="compile"))
    with pytest.raises(ValueError, match="invalid record id"):
        runs.create(run_record("../run_1"))
    assert runs.list() == []


def test_registry_run_record_round_trip(tmp_path):
    runs = FileRunRegistry(tmp_path)
    now = datetime(2026, 9, 22, 21, 50, 1, 100_000, tzinfo=UTC)
    record = RunRecord(
        id="run_1", process="process_supplier_invoice", status="running", created_at=now, updated_at=now,
        started_at=now, mode="local", inputs={"pdf_path": "examples/a.pdf"}, usage=Usage(input_tokens=5, calls=1),
        meta={"trigger": "manual", "release_id": None, "target": {"kind": "local"}},
    )
    runs.create(record.model_dump())                       # python mode: datetimes and models are converted
    assert RunRecord.model_validate(runs.get("run_1")) == record
    runs.update("run_1", {"status": "succeeded", "exit": "done", "finished_at": now, "usage": Usage(calls=2)})
    back = RunRecord.model_validate(runs.get("run_1"))
    assert (back.status, back.exit, back.finished_at, back.usage.calls) == ("succeeded", "done", now, 2)


def test_registry_update_is_a_shallow_merge_that_sets_updated_at(tmp_path, monkeypatch):
    runs = FileRunRegistry(tmp_path)
    monkeypatch.setattr(local, "_now", lambda: "2026-09-22T10:00:00Z")
    runs.create(run_record("run_1", meta={"a": 1, "b": 2}, inputs={"x": 1}))
    monkeypatch.setattr(local, "_now", lambda: "2026-09-22T10:00:05Z")
    merged = runs.update("run_1", {"status": "running", "meta": {"c": 3}, "updated_at": "ignored"})
    assert merged == runs.get("run_1")
    assert merged["status"] == "running"
    assert merged["meta"] == {"c": 3}                      # nested values are replaced, not merged
    assert merged["inputs"] == {"x": 1}
    assert merged["created_at"] == "2026-09-22T10:00:00Z"
    assert merged["updated_at"] == "2026-09-22T10:00:05Z"


def test_registry_update_and_get_of_unknown_ids(tmp_path):
    runs = FileRunRegistry(tmp_path)
    with pytest.raises(KeyError):
        runs.update("run_missing", {"status": "running"})
    assert runs.get("run_missing") is None
    assert runs.get("../records/x") is None


def test_registry_list_filters_newest_first_with_limit(tmp_path):
    runs = FileRunRegistry(tmp_path)
    runs.create(run_record("run_1", process="a", status="succeeded", created_at="2026-09-22T09:00:00Z"))
    runs.create(run_record("job_1", kind="job", process="a", status="running", created_at="2026-09-22T10:00:00Z"))
    # 12:00+02:00 is 10:00Z: ordering is by instant, not by string
    runs.create(run_record("run_2", process="b", status="failed", created_at="2026-09-22T12:00:00+02:00"))
    runs.create(run_record("run_3", process="a", status="succeeded", created_at="2026-09-22T11:00:00Z"))
    runs.create(run_record("run_4", process="b", status="succeeded", created_at="2026-09-22T08:00:00Z"))

    def ids(records):
        return [r["id"] for r in records]

    assert ids(runs.list()) == ["run_3", "run_2", "job_1", "run_1", "run_4"]   # equal instants: id descending
    assert ids(runs.list(kind="run")) == ["run_3", "run_2", "run_1", "run_4"]
    assert ids(runs.list(kind="job")) == ["job_1"]
    assert ids(runs.list(process="b")) == ["run_2", "run_4"]
    assert ids(runs.list(status="succeeded")) == ["run_3", "run_1", "run_4"]
    assert ids(runs.list(kind="run", process="a", status="succeeded")) == ["run_3", "run_1"]
    assert ids(runs.list(limit=2)) == ["run_3", "run_2"]
    assert ids(runs.list(process="nope")) == []


def test_registry_list_of_an_empty_registry(tmp_path):
    assert FileRunRegistry(tmp_path / "never-created").list() == []


def test_registry_writes_one_file_per_record_and_no_index(tmp_path):
    runs = FileRunRegistry(tmp_path)
    for i in range(3):
        runs.create(run_record(f"run_{i}"))
        runs.update(f"run_{i}", {"status": "running"})
    assert sorted(p.name for p in (tmp_path / "records").iterdir()) == ["run_0.json", "run_1.json", "run_2.json"]
    assert stray_files(tmp_path) == []


def test_registry_test_results_round_trip(tmp_path):
    runs = FileRunRegistry(tmp_path)
    key = "step:process_supplier_invoice#read_pdf:sha256:" + "ab" * 32
    result = TestResult(commit="c0ffee", key=key, passed=True, counts={"passed": 3, "failed": 0},
                        ran_at=datetime(2026, 9, 22, tzinfo=UTC), job_id="job_1", report={"cases": []})
    runs.put_test_result(result)
    assert runs.get_test_result("c0ffee", key) == result
    assert (tmp_path / "tests" / "c0ffee" / f"{hashlib.sha256(key.encode()).hexdigest()[:32]}.json").is_file()
    assert runs.get_test_result("deadbeef", key) is None
    assert runs.get_test_result("c0ffee", "process:other:sha256:00") is None
    assert runs.get_test_result("../c0ffee", key) is None
    failed = result.model_copy(update={"passed": False, "counts": {"passed": 2, "failed": 1}})
    runs.put_test_result(failed)                           # a newer result replaces the old one
    assert runs.get_test_result("c0ffee", key) == failed
    assert stray_files(tmp_path) == []


def test_registry_concurrent_creates_of_one_id_admit_exactly_one(tmp_path):
    runs = FileRunRegistry(tmp_path)
    outcomes: list[str] = []
    barrier = threading.Barrier(8)

    def create(k: int) -> None:
        barrier.wait()
        try:
            runs.create(run_record("run_1", meta={"by": k}))
            outcomes.append("created")
        except FileExistsError:
            outcomes.append("exists")

    threads = [threading.Thread(target=create, args=(k,)) for k in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["created"] + ["exists"] * 7


def test_registry_concurrent_updates_from_two_threads_lose_no_field(tmp_path):
    runs = FileRunRegistry(tmp_path)
    runs.create(run_record("run_1"))
    barrier = threading.Barrier(2)

    def updater(tag: str) -> None:
        barrier.wait()
        for i in range(150):
            runs.update("run_1", {f"{tag}_{i}": i})

    threads = [threading.Thread(target=updater, args=(tag,)) for tag in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    got = runs.get("run_1")
    assert {f"{tag}_{i}": i for tag in ("a", "b") for i in range(150)}.items() <= got.items()
    assert stray_files(tmp_path) == []


UPDATER = """
import sys, time
from pathlib import Path
from wynd.runtime.storage.local import FileRunRegistry
root, tag, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
runs = FileRunRegistry(root)
Path(root, f"ready-{tag}").touch()
while not Path(root, "go").exists():
    time.sleep(0.005)
for i in range(n):
    runs.update("run_1", {f"{tag}_{i}": i})
"""


def test_registry_concurrent_updates_from_two_processes_lose_no_field(tmp_path):
    runs = FileRunRegistry(tmp_path)
    runs.create(run_record("run_1"))
    n = 200
    procs = [subprocess.Popen([sys.executable, "-c", UPDATER, str(tmp_path), tag, str(n)]) for tag in ("p", "q")]
    deadline = time.monotonic() + 60
    while not all((tmp_path / f"ready-{tag}").exists() for tag in ("p", "q")):
        assert time.monotonic() < deadline, "updater processes did not start"
        assert all(p.poll() is None for p in procs), "an updater process exited early"
        time.sleep(0.01)
    (tmp_path / "go").touch()
    for i in range(n):                                     # the parent races them as well
        runs.update("run_1", {f"parent_{i}": i})
    assert [p.wait(timeout=60) for p in procs] == [0, 0]
    got = runs.get("run_1")
    assert {f"{tag}_{i}": i for tag in ("p", "q", "parent") for i in range(n)}.items() <= got.items()
    assert stray_files(tmp_path) == []


# --- FileRegistry ------------------------------------------------------------------------------------------------------


def test_user_registry_missing_files_are_empty_and_nothing_is_created(tmp_path):
    reg = FileRegistry(tmp_path / "home")
    assert reg.list("mcp") == {}
    assert reg.get("providers", "claude-code") is None
    assert reg.secrets() == {}
    assert not (tmp_path / "home").exists()               # reads never create WYND_HOME
    assert reg.remove("registries", "local") is False


def test_user_registry_put_get_list_remove(tmp_path):
    reg = FileRegistry(tmp_path)
    github = {"name": "github", "transport": "http", "url": "https://api.githubcopilot.com/mcp/",
              "headers": {"Authorization": "Bearer ${env:GITHUB_TOKEN}"}, "auth_env": ["GITHUB_TOKEN"]}
    reg.put("mcp", "github", github)
    reg.put("mcp", "local-fs", {"name": "local-fs", "transport": "stdio", "command": ["fs-server"]})
    reg.put("providers", "claude-code", {"tiers": {"cheap": "sonnet"}})
    assert reg.get("mcp", "github") == github
    assert set(reg.list("mcp")) == {"github", "local-fs"}
    assert reg.list("providers") == {"claude-code": {"tiers": {"cheap": "sonnet"}}}
    assert json.loads((tmp_path / "mcp.json").read_text()) == {"version": 1, "entries": reg.list("mcp")}

    reg.put("providers", "claude-code", {"tiers": {"cheap": "haiku"}})      # put replaces the entry
    assert reg.get("providers", "claude-code") == {"tiers": {"cheap": "haiku"}}

    assert reg.remove("mcp", "github") is True
    assert reg.remove("mcp", "github") is False
    assert set(reg.list("mcp")) == {"local-fs"}
    assert stray_files(tmp_path) == []


def test_user_registry_returns_copies(tmp_path):
    reg = FileRegistry(tmp_path)
    reg.put("providers", "fake", {"tiers": {"cheap": "fake"}})
    reg.get("providers", "fake")["tiers"]["cheap"] = "changed"
    assert reg.get("providers", "fake") == {"tiers": {"cheap": "fake"}}


@pytest.mark.parametrize("name", ["", "-x", ".x", "a/b", "../x", "a b"])
def test_user_registry_rejects_invalid_names(tmp_path, name):
    with pytest.raises(ValueError, match="invalid registry entry name"):
        FileRegistry(tmp_path).put("mcp", name, {})


@pytest.mark.parametrize("section", ["secrets", "mcps", "", "../mcp"])
def test_user_registry_rejects_unknown_sections(tmp_path, section):
    reg = FileRegistry(tmp_path)
    for call in (lambda: reg.list(section), lambda: reg.get(section, "x"), lambda: reg.put(section, "x", {}),
                 lambda: reg.remove(section, "x")):
        with pytest.raises(ValueError, match="unknown registry section"):
            call()


def test_user_registry_secrets_file_is_0600_and_round_trips(tmp_path):
    reg = FileRegistry(tmp_path / "home")
    values = {
        "WYND_MCP_GITHUB_TOKEN": "gho_abc123",
        "WITH_SPACES": "p@ss word",
        "QUOTED": 'say "hi" \\ bye',
        "MULTILINE": "line1\nline2",
        "EQUALS": "a=b=c",
        "UNICODE": "café",
        "EMPTY": "",
    }
    for name, value in values.items():
        reg.put_secret(name, value)
    path = tmp_path / "home" / "secrets.env"
    assert mode_of(path) == 0o600
    assert reg.secrets() == values
    reg.put_secret("WYND_MCP_GITHUB_TOKEN", "gho_rotated")
    assert mode_of(path) == 0o600
    assert reg.secrets() == {**values, "WYND_MCP_GITHUB_TOKEN": "gho_rotated"}
    assert stray_files(tmp_path) == []


def test_user_registry_reads_a_hand_written_secrets_file(tmp_path):
    (tmp_path / "secrets.env").write_text('# tokens\n\nPLAIN=abc\nQUOTED="x y"\n  SPACED = v \nnot a line\n')
    assert FileRegistry(tmp_path).secrets() == {"PLAIN": "abc", "QUOTED": "x y", "SPACED": "v"}


@pytest.mark.parametrize("name", ["", "1ABC", "A-B", "A B", "A=B"])
def test_user_registry_rejects_invalid_secret_names(tmp_path, name):
    with pytest.raises(ValueError, match="invalid secret name"):
        FileRegistry(tmp_path).put_secret(name, "v")


def test_user_registry_location(tmp_path):
    assert FileRegistry(tmp_path / "home").location() == str(tmp_path / "home")


def test_user_registry_concurrent_puts_lose_no_entry(tmp_path):
    reg = FileRegistry(tmp_path)
    barrier = threading.Barrier(4)

    def putter(k: int) -> None:
        barrier.wait()
        for i in range(25):
            reg.put("registries", f"r{k}-{i}", {"name": f"r{k}-{i}", "url": "localhost:5001/wynd"})
            reg.put_secret(f"S{k}_{i}", str(i))

    threads = [threading.Thread(target=putter, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(reg.list("registries")) == 100
    assert len(reg.secrets()) == 100


# --- EnvRegistry (registry.env) ----------------------------------------------------------------------------------------

SNAPSHOT = {
    "mcp": {"github": {"name": "github", "transport": "http", "url": "https://api.githubcopilot.com/mcp/",
                       "headers": {"Authorization": "Bearer ${env:GITHUB_TOKEN}"}, "auth_env": ["GITHUB_TOKEN"]}},
    "providers": {"claude-code": {"tiers": {"cheap": "sonnet"}}},
}


def env_registry_env(value: str | None) -> dict[str, str]:
    env = {"WYND_REGISTRY": "env"}
    if value is not None:
        env["WYND_REGISTRY_JSON"] = value
    return env


def test_env_registry_reads_wynd_registry_json():
    reg = registry_from_env(env_registry_env(json.dumps(SNAPSHOT, separators=(",", ":"))))
    assert isinstance(reg, EnvRegistry)
    assert reg.list("mcp") == SNAPSHOT["mcp"]
    assert reg.get("providers", "claude-code") == {"tiers": {"cheap": "sonnet"}}
    assert reg.get("mcp", "missing") is None
    assert reg.list("registries") == {}
    assert reg.secrets() == {}
    assert reg.location() == "env:WYND_REGISTRY_JSON"
    reg.get("providers", "claude-code")["tiers"]["cheap"] = "changed"
    assert reg.get("providers", "claude-code") == {"tiers": {"cheap": "sonnet"}}
    with pytest.raises(ValueError, match="unknown registry section"):
        reg.list("secrets")


def test_env_registry_refuses_writes():
    reg = EnvRegistry({"WYND_REGISTRY_JSON": json.dumps(SNAPSHOT)})
    for call in (lambda: reg.put("mcp", "x", {}), lambda: reg.remove("mcp", "github"),
                 lambda: reg.put_secret("TOKEN", "v")):
        with pytest.raises(StorageConfigError) as err:
            call()
        assert str(err.value) == READ_ONLY
    assert reg.get("mcp", "github") == SNAPSHOT["mcp"]["github"]


@pytest.mark.parametrize("value", [None, "", "  "])
def test_env_registry_unset_or_empty_has_no_entries(value):
    reg = registry_from_env(env_registry_env(value))
    assert all(reg.list(section) == {} for section in ("mcp", "providers", "registries"))


@pytest.mark.parametrize("value", ["{not json", "[1, 2]", '"text"', '{"mcp": []}'])
def test_env_registry_rejects_malformed_json(value):
    with pytest.raises(StorageConfigError, match="WYND_REGISTRY_JSON"):
        registry_from_env(env_registry_env(value))


def test_env_registry_selected_through_stores_from_os_environ(tmp_path, monkeypatch):
    monkeypatch.setenv("WYND_REGISTRY", "env")
    monkeypatch.setenv("WYND_REGISTRY_JSON", json.dumps(SNAPSHOT))
    stores = stores_from_env(data_dir=tmp_path)
    assert isinstance(stores.registry, EnvRegistry)
    assert stores.registry.list("providers") == SNAPSHOT["providers"]


# --- selection: stores_from_env / registry_from_env -------------------------------------------------------------------


def test_stores_defaults_use_data_dir_and_wynd_home(tmp_path):
    home = tmp_path / "home"
    stores = stores_from_env({"WYND_HOME": str(home)}, data_dir=tmp_path / "data")
    assert isinstance(stores, Stores)
    assert isinstance(stores.workspaces, FileWorkspaceStore)
    assert isinstance(stores.traces, JsonlTraceSink)
    assert isinstance(stores.runs, FileRunRegistry)
    assert isinstance(stores.registry, FileRegistry)
    assert stores.registry.location() == str(home)
    assert not (tmp_path / "data").exists() and not home.exists()      # backends create directories lazily

    ws = stores.workspaces.open("run_1")
    stores.traces.write(event("run_1", 1))
    stores.runs.create(run_record("run_1"))
    stores.registry.put("providers", "fake", {"tiers": {}})
    assert ws == tmp_path / "data" / "workspaces" / "run_1"
    assert (tmp_path / "data" / "traces" / "run_1.jsonl").is_file()
    assert (tmp_path / "data" / "registry" / "records" / "run_1.json").is_file()
    assert (home / "providers.json").is_file()


def test_stores_wynd_data_dir_wins_over_data_dir(tmp_path):
    stores = stores_from_env({"WYND_DATA_DIR": str(tmp_path / "env")}, data_dir=tmp_path / "arg")
    assert stores.workspaces.open("run_1") == tmp_path / "env" / "workspaces" / "run_1"
    assert stores.traces.uri("run_1") == (tmp_path / "env" / "traces" / "run_1.jsonl").as_uri()


def test_stores_without_data_dir_raise(tmp_path):
    with pytest.raises(StorageConfigError, match="WYND_DATA_DIR is not set"):
        stores_from_env({"WYND_HOME": str(tmp_path)})


def test_stores_locations_override_roots_and_need_no_data_dir(tmp_path):
    env = {
        "WYND_WORKSPACE_STORE": f"file:{tmp_path / 'w'}",
        "WYND_TRACE_SINK": f"jsonl:{tmp_path / 't'}",
        "WYND_RUN_REGISTRY": f"file:{tmp_path / 'r'}",
        "WYND_REGISTRY": f"file:{tmp_path / 'h'}",
    }
    stores = stores_from_env(env)
    assert stores.workspaces.open("run_1") == tmp_path / "w" / "run_1"
    stores.traces.write(event("run_1", 1))
    assert (tmp_path / "t" / "run_1.jsonl").is_file()
    stores.runs.create(run_record("run_1"))
    assert (tmp_path / "r" / "records" / "run_1.json").is_file()
    assert stores.registry.location() == str(tmp_path / "h")


def test_stores_empty_location_means_default_root(tmp_path):
    stores = stores_from_env({"WYND_TRACE_SINK": "jsonl:", "WYND_HOME": str(tmp_path)}, data_dir=tmp_path)
    assert stores.traces.uri("run_1") == (tmp_path / "traces" / "run_1.jsonl").as_uri()


@pytest.mark.parametrize("var", ["WYND_WORKSPACE_STORE", "WYND_TRACE_SINK", "WYND_RUN_REGISTRY", "WYND_REGISTRY"])
def test_stores_unknown_scheme_raises(tmp_path, var):
    with pytest.raises(StorageConfigError, match=f"unknown {var} scheme 's3'"):
        stores_from_env({var: "s3:bucket/prefix", "WYND_HOME": str(tmp_path)}, data_dir=tmp_path)


def test_stores_read_os_environ_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("WYND_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WYND_HOME", str(tmp_path / "home"))
    stores = stores_from_env()
    assert stores.workspaces.open("run_1") == tmp_path / "data" / "workspaces" / "run_1"
    assert stores.registry.location() == str(tmp_path / "home")


def test_wynd_home_falls_back_to_home_dot_wynd(tmp_path, monkeypatch):
    monkeypatch.delenv("WYND_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert registry_from_env().location() == str(tmp_path / ".wynd")
    assert registry_from_env({}).location() == str(tmp_path / ".wynd")
    assert registry_from_env({"WYND_HOME": ""}).location() == str(tmp_path / ".wynd")
    stores = stores_from_env(data_dir=tmp_path / "data")
    assert stores.registry.location() == str(tmp_path / ".wynd")
    assert not (tmp_path / ".wynd").exists()                             # nothing is created until a write
    stores.registry.put_secret("TOKEN", "v")
    assert mode_of(tmp_path / ".wynd" / "secrets.env") == 0o600


def test_wynd_home_set_wins_over_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    assert registry_from_env({"WYND_HOME": str(tmp_path / "wh")}).location() == str(tmp_path / "wh")


def test_builtin_backends_are_registered_entry_points():
    names = {ep.name: ep.value for ep in importlib.metadata.entry_points(group="wynd.storage")}
    assert names == {
        "workspace.file": "wynd.runtime.storage.local:workspace_file",
        "trace.jsonl": "wynd.runtime.storage.local:trace_jsonl",
        "runs.file": "wynd.runtime.storage.local:runs_file",
        "registry.file": "wynd.runtime.storage.local:registry_file",
        "registry.env": "wynd.runtime.storage.local:registry_env",
    }


class FakeEntryPoint:
    def __init__(self, factory):
        self.factory = factory

    def load(self):
        return self.factory


def test_entry_point_supplies_an_alternative_backend(tmp_path, monkeypatch):
    calls = []

    class MemorySink:
        def __init__(self):
            self.events = []

        def write(self, event):
            self.events.append(event)

    def trace_memory(*, location, root, env):
        calls.append((location, root, env))
        return MemorySink()

    real = importlib.metadata.entry_points

    def entry_points(**kw):
        if kw == {"group": "wynd.storage", "name": "trace.memory"}:
            return [FakeEntryPoint(trace_memory)]
        return real(**kw)

    monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)
    env = {"WYND_TRACE_SINK": "memory:bucket/prefix", "WYND_HOME": str(tmp_path)}
    stores = stores_from_env(env, data_dir=tmp_path)
    assert isinstance(stores.traces, MemorySink)
    assert calls == [("bucket/prefix", None, env)]
    assert isinstance(stores.workspaces, FileWorkspaceStore)           # the other interfaces keep their defaults

    calls.clear()
    stores_from_env({"WYND_TRACE_SINK": "memory", "WYND_HOME": str(tmp_path)}, data_dir=tmp_path)
    assert calls[0][:2] == (None, tmp_path / "traces")


def test_local_factories_need_a_location_or_root():
    with pytest.raises(StorageConfigError, match="WYND_DATA_DIR is not set"):
        local.runs_file(None, None, {})
    with pytest.raises(StorageConfigError, match="WYND_HOME is not set"):
        local.registry_file(None, None, {})


def test_storage_env_lists_the_selectors():
    by_name = {v.name: v for v in STORAGE_ENV}
    assert list(by_name) == [
        "WYND_DATA_DIR", "WYND_WORKSPACE_STORE", "WYND_TRACE_SINK", "WYND_RUN_REGISTRY", "WYND_REGISTRY", "WYND_HOME",
    ]
    assert {name: v.default for name, v in by_name.items()} == {
        "WYND_DATA_DIR": None, "WYND_WORKSPACE_STORE": "file", "WYND_TRACE_SINK": "jsonl",
        "WYND_RUN_REGISTRY": "file", "WYND_REGISTRY": "file", "WYND_HOME": "~/.wynd",
    }
    assert all(not v.required and not v.secret and v.used_by == ["storage"] for v in STORAGE_ENV)


def test_storage_package_imports_no_backend():
    code = "import sys, wynd.runtime.storage; print('wynd.runtime.storage.local' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, env=os.environ)
    assert out.stdout.strip() == "False"

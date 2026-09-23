"""Every YAML document of the sample workspace `examples/invoices` loads with the public `wynd.spec` loaders
(PLAN §4.3, M1-INT): no load errors, no document findings, an authoring round trip that changes nothing, and the
cross-document facts the loaders alone cannot see (names match paths, locks match their protos)."""

from pathlib import Path

import pytest

from wynd.spec import (
    EdgesLock,
    ProcessDoc,
    ProtoStep,
    StepLock,
    WorkspaceConfig,
    check_process_doc,
    check_proto_step,
    interfaces_equivalent,
    load_edges_lock,
    load_process,
    load_proto_step,
    load_step_lock,
    load_workspace_config,
    parse_use,
    proto_hash,
)
from wynd.spec.workspace import (
    EDGES_LOCK_FILE,
    PROCESS_FILE,
    PROTO_DIR,
    STATE_DIR,
    STEP_LOCK_FILE,
    STEP_PROTO_FILE,
    STEPS_DIR,
    WORKSPACE_FILE,
)
from wynd.spec.yamlio import dump_yaml, parse_model, yaml_to_json

WS = Path(__file__).resolve().parents[3] / "examples" / "invoices"


def yaml_files() -> list[Path]:
    return sorted(
        p for p in WS.rglob("*") if p.suffix in (".yaml", ".yml") and STATE_DIR not in p.relative_to(WS).parts
    )


def kind_of(path: Path) -> str | None:
    """The document kind a sample YAML file must be, from its place in the SPEC §5.1 layout."""
    rel = path.relative_to(WS)
    match rel.parts:
        case (name,) if name == WORKSPACE_FILE:
            return "workspace"
        case (*_, name) if name == PROCESS_FILE:
            return "process"
        case (*_, name) if name == STEP_LOCK_FILE:
            return "step_lock"
        case (*_, name) if name == EDGES_LOCK_FILE:
            return "edges_lock"
        case (*_, name) if name == STEP_PROTO_FILE:
            return "proto"
        case (*_, parent, _) if parent == PROTO_DIR:
            return "proto"
    return None


LOADERS = {
    "workspace": (load_workspace_config, WorkspaceConfig),
    "process": (load_process, ProcessDoc),
    "proto": (load_proto_step, ProtoStep),
    "step_lock": (load_step_lock, StepLock),
    "edges_lock": (load_edges_lock, EdgesLock),
}
FILES = yaml_files()
IDS = [str(p.relative_to(WS)) for p in FILES]
PROCESS_FILES = [p for p in FILES if kind_of(p) == "process"]
LOCK_FILES = [p for p in FILES if kind_of(p) == "step_lock"]


def test_the_sample_has_the_m1_documents():
    kinds = [kind_of(p) for p in FILES]
    assert kinds.count("workspace") == 1
    assert kinds.count("process") == 1
    assert kinds.count("proto") == 6
    assert kinds.count("step_lock") == 6


@pytest.mark.parametrize("path", FILES, ids=IDS)
def test_every_yaml_file_is_a_known_document(path):
    assert kind_of(path) is not None, f"{path.relative_to(WS)} is not a document of the SPEC §5.1 layout"


@pytest.mark.parametrize("path", FILES, ids=IDS)
def test_loads_checks_and_round_trips(path):
    kind = kind_of(path)
    load, model = LOADERS[kind]
    doc = load(path)

    match kind:
        case "process":
            assert check_process_doc(doc) == []
        case "proto":
            assert check_proto_step(doc) == []

    if hasattr(doc, "to_authoring"):
        again = parse_model(dump_yaml(doc.to_authoring()), model)
        assert again == doc
        if kind == "proto":
            assert proto_hash(again) == proto_hash(doc)

    data, problem = yaml_to_json(path.read_text(encoding="utf-8"), str(path.relative_to(WS)))
    assert problem is None
    assert isinstance(data, dict)


@pytest.mark.parametrize("path", PROCESS_FILES, ids=[str(p.relative_to(WS)) for p in PROCESS_FILES])
def test_process_names_and_step_references_resolve(path):
    config = load_workspace_config(WS / WORKSPACE_FILE)
    doc = load_process(path)
    process_dir = path.parent
    pid = next(
        process_dir.relative_to(WS / root).as_posix()
        for root in config.process_roots
        if process_dir.is_relative_to(WS / root)
    )
    assert doc.name == pid.rsplit("/", 1)[-1]
    assert doc.entry in doc.steps
    for key, ref in doc.steps.items():
        use = parse_use(ref.use)
        assert use.form == "local", f"steps.{key}: the M1 sample only uses process-local steps"
        name = Path(use.target).name
        assert (process_dir / STEPS_DIR / name / STEP_LOCK_FILE).is_file(), f"steps.{key}: no compiled package"
        assert (process_dir / PROTO_DIR / f"{name}.yaml").is_file(), f"steps.{key}: no proto-step"


@pytest.mark.parametrize("path", LOCK_FILES, ids=[str(p.relative_to(WS)) for p in LOCK_FILES])
def test_step_locks_match_their_package_and_proto(path):
    lock = load_step_lock(path)
    package = path.parent
    assert lock.name == package.name
    module, _, cls = lock.entrypoint.partition(":")
    source = package / f"{module.replace('.', '/')}.py"
    assert source.is_file()
    assert f"class {cls}(" in source.read_text(encoding="utf-8")

    proto = load_proto_step(package.parents[1] / PROTO_DIR / f"{package.name}.yaml")
    assert proto.name == package.name
    assert lock.proto_hash == proto_hash(proto)
    assert lock.interface is not None
    assert interfaces_equivalent(lock.interface, proto.interface()) == []
    assert set(lock.interface.exits) == set(proto.exits)

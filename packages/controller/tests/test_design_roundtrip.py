"""The web's design round trip is lossless (`$DRAFTS/06 §11.2`, `$DRAFTS/07` C-SPEC-1).

`DesignService.get` -> JSON over HTTP -> `DesignService.save` -> reload of the dogfood `process.yaml` and protos
changes no document, no model and no proto hash, so a save without edits never makes a compiled step stale;
`exit_codes` int keys survive JSON's string keys."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from wynd.controller.api.models_web import SaveRequest
from wynd.spec.hashing import proto_hash
from wynd.spec.process_doc import ProcessDoc
from wynd.spec.proto_step import ProtoStep
from wynd.spec.yamlio import parse_model, yaml_to_json

DOGFOOD = Path(__file__).resolve().parents[3] / "examples/invoices/processes/process_supplier_invoice"
PID = "process_supplier_invoice"
PROCESS_YAML = f"processes/{PID}/process.yaml"


def dogfood_files() -> dict[str, bytes]:
    return {
        f"processes/{PID}/{path.relative_to(DOGFOOD).as_posix()}": path.read_bytes()
        for path in sorted(DOGFOOD.rglob("*"))
        if path.is_file() and not {"__pycache__", ".pytest_cache"} & set(path.parts)
    }


def over_http(design) -> dict:
    """The `DesignDoc` as the web receives it."""
    return json.loads(design.model_dump_json())


def save_everything(controller, pid: str) -> dict:
    """Save every document of the design back unchanged, exactly as the web would send it."""
    design = over_http(controller.design.get(pid))
    files = [design["process_file"], *design["protos"].values()]
    req = SaveRequest.model_validate(json.loads(json.dumps({
        "writes": [{"path": f["path"], "base_revision": f["revision"], "doc": f["doc"]} for f in files],
        "commit": None,
    })))
    return over_http(controller.design.save(pid, req))


def model(path: str, text: str):
    return parse_model(text, ProcessDoc if path.endswith("/process.yaml") else ProtoStep, path)


def test_dogfood_design_round_trip_is_lossless(make_workspace, make_controller):
    workspace = make_workspace(files=dogfood_files())
    controller = make_controller(workspace)
    before = {path: (workspace / path).read_text() for path in controller.design.scope(PID)}
    assert PROCESS_YAML in before and len(before) == 7                         # process.yaml + six local protos
    phases = {key: step.phase for key, step in controller.processes.steps(PID).items()}
    assert set(phases.values()) == {"compiled"}
    issues = [(i.code, i.loc) for i in controller.processes.validate(PID).issues]

    result = save_everything(controller, PID)

    assert sorted(f["path"] for f in result["files"]) == sorted(before)
    for path, old in before.items():
        new = (workspace / path).read_text()
        assert yaml_to_json(new, path) == yaml_to_json(old, path)               # the web sees the same document
        assert model(path, new).model_dump(mode="json") == model(path, old).model_dump(mode="json")
        if path != PROCESS_YAML:
            assert proto_hash(model(path, new)) == proto_hash(model(path, old))
    record = yaml_to_json((workspace / PROCESS_YAML).read_text(), PROCESS_YAML)[0]["examples"][0]["outputs"]["record"]
    assert record["due_date"] == "2026-10-01" and record["total"] == 1200.5    # dates and floats survive
    assert {key: step.phase for key, step in controller.processes.steps(PID).items()} == phases
    assert [(i["code"], i["loc"]) for i in result["validation"]["issues"]] == issues

    saved = {path: (workspace / path).read_bytes() for path in before}
    second = save_everything(controller, PID)                                   # a fixed point from here on
    assert {path: (workspace / path).read_bytes() for path in before} == saved
    assert {f["path"]: f["revision"] for f in second["files"]} == {f["path"]: f["revision"] for f in result["files"]}


def test_exit_codes_int_keys_survive(workspace, controller, commit):
    path = "processes/p1/proto/shell.yaml"
    text = ("kind: proto_step\nname: shell\ninstruction: Run it.\n"
            "exit_codes:\n  0: done\n  3: empty\n  '*': error\nexamples: []\n")
    commit(workspace, "shell proto", {path: text})
    assert over_http(controller.design.get("p1"))["protos"][path]["doc"]["exit_codes"] == {
        "0": "done", "3": "empty", "*": "error"}                                 # JSON object keys are strings

    save_everything(controller, "p1")

    new = (workspace / path).read_text()
    assert yaml.safe_load(new)["exit_codes"] == {0: "done", 3: "empty", "*": "error"}
    assert parse_model(new, ProtoStep, path).exit_codes == parse_model(text, ProtoStep, path).exit_codes
    assert new == text                                                          # byte-identical for block-style YAML

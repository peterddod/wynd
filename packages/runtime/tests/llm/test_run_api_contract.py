"""Run API contract snapshot (PLAN §3.17; `$DRAFTS/03 §16` test_run_api_contract): the exported JSON Schema equals
the committed `supervisor/run_api.schema.json`, so contract drift fails CI. Regenerate deliberately with
`uv run wynd-supervisor schema > packages/runtime/src/wynd/runtime/supervisor/run_api.schema.json`."""

from __future__ import annotations

import json
from importlib import resources

from wynd.runtime.supervisor.main import main
from wynd.runtime.supervisor.schema import export_json_schema

COMMITTED = resources.files("wynd.runtime.supervisor") / "run_api.schema.json"


def test_exported_schema_equals_the_committed_snapshot():
    assert export_json_schema() == json.loads(COMMITTED.read_text())


def test_the_schema_command_prints_the_committed_file_byte_for_byte(capsys):
    assert main(["schema"]) == 0
    assert capsys.readouterr().out == COMMITTED.read_text()


def test_the_snapshot_covers_every_run_api_model_and_its_key_fields():
    schema = json.loads(COMMITTED.read_text())
    defs = schema["$defs"]
    assert schema["api_version"] == "1"
    assert {"RunRequest", "RunCreated", "Run", "RunSummary", "ProcessInfo", "ErrorBody", "Links", "FileUpload",
            "UsageTotals"} <= set(defs)
    request = defs["RunRequest"]
    assert request["required"] == ["inputs"] and request["additionalProperties"] is False
    assert set(request["properties"]) == {"inputs", "run_id", "files", "metadata"}
    assert defs["RunCreated"]["properties"]["status"]["enum"] == ["queued", "running", "succeeded", "failed"]
    assert set(defs["Run"]["properties"]) == {
        "api_version", "run_id", "process", "commit", "runtime_version", "mode", "status", "exit", "outputs", "error",
        "created_at", "started_at", "finished_at", "duration_ms", "usage", "metadata", "links"}
    assert set(defs["ProcessInfo"]["properties"]) == {
        "api_version", "process", "name", "goal", "commit", "runtime_version", "inputs_schema", "outputs_schema",
        "steps", "limits"}
    assert set(defs["Links"]["properties"]) == {"self", "events", "outputs"}

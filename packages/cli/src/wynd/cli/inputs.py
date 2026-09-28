"""Run inputs and compile answers from the command line (PLAN §9, §3.22; `$DRAFTS/06 §9.1` "Inputs", §10.6).

Inputs merge the file (`--inputs-file PATH|-`, JSON or YAML), then `--inputs '<json object>'`, then each
`--input KEY=VALUE` (the value parsed with `yaml.safe_load`, so `3` is an int and `true` a bool; an empty or blank
value stays text; YAML dates become ISO text). The result must be a mapping. Top-level `path`-typed fields (per
`ctl.processes.interface(pid)`, JSON Schema `format: "path"`, also optional and `list[path]` fields) are made
absolute against the cwd. Answers: a YAML mapping of question id -> answer text, overridden by `--answer QID=TEXT`.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from wynd.controller.errors import Invalid

if TYPE_CHECKING:
    from wynd.controller import Controller


def parse_inputs(
    ctl: Controller,
    pid: str,
    *,
    pairs: Sequence[str] = (),
    inputs_json: str | None = None,
    inputs_file: str | None = None,
    cwd: Path | None = None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if inputs_file is not None:
        merged.update(_mapping(_load_yaml(_read(inputs_file), f"--inputs-file {inputs_file}"),
                               f"--inputs-file {inputs_file}"))
    if inputs_json is not None:
        try:
            merged.update(_mapping(json.loads(inputs_json), "--inputs"))
        except json.JSONDecodeError as error:
            raise Invalid(f"--inputs is not valid JSON: {error}") from None
    for pair in pairs:
        key, value = _split(pair, "--input", "KEY=VALUE")
        merged[key] = _plain(_scalar(value))
    if not merged:
        return merged
    return _absolute_paths(ctl, pid, merged, Path.cwd() if cwd is None else Path(cwd))


def parse_answers(answers_file: Path | None = None, pairs: Sequence[str] = ()) -> dict[str, str]:
    answers: dict[str, str] = {}
    if answers_file is not None:
        data = _load_yaml(_read(str(answers_file)), f"--answers {answers_file}")
        answers.update({str(qid): _answer_text(text)
                        for qid, text in _mapping(data or {}, f"--answers {answers_file}").items()})
    for pair in pairs:
        qid, text = _split(pair, "--answer", "QID=TEXT")
        answers[qid] = text
    return answers


def _read(source: str) -> str:
    if source == "-":
        return sys.stdin.read()
    try:
        return Path(source).read_text(encoding="utf-8")
    except OSError as error:
        raise Invalid(f"cannot read {source}: {error.strerror or error}") from None


def _load_yaml(text: str, source: str) -> Any:
    try:
        return _plain(yaml.safe_load(text))
    except yaml.YAMLError as error:
        raise Invalid(f"{source} is not valid JSON or YAML: {error}") from None


def _mapping(value: Any, source: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise Invalid(f"{source} must be a mapping of names to values, got {type(value).__name__}")
    return {str(key): item for key, item in value.items()}


def _split(pair: str, option: str, form: str) -> tuple[str, str]:
    key, sep, value = pair.partition("=")
    if not sep or not key.strip():
        raise Invalid(f"{option} expects {form}, got {pair!r}")
    return key.strip(), value


def _scalar(text: str) -> Any:
    """YAML scalar parsing, except that an empty or blank value stays the text given (not null)."""
    if not text.strip():
        return text
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def _plain(value: Any) -> Any:
    """YAML dates/datetimes become ISO text (inputs travel as JSON)."""
    match value:
        case dict():
            return {key: _plain(item) for key, item in value.items()}
        case list():
            return [_plain(item) for item in value]
        case datetime() | date():
            return value.isoformat()
    return value


def _answer_text(value: Any) -> str:
    match value:
        case str():
            return value
        case bool():
            return "yes" if value else "no"
        case None:
            return ""
        case dict() | list():
            return json.dumps(value, ensure_ascii=False)
    return str(value)


def _absolute_paths(ctl: Controller, pid: str, inputs: dict[str, Any], cwd: Path) -> dict[str, Any]:
    properties = (ctl.processes.interface(pid).inputs or {}).get("properties", {})
    out = dict(inputs)
    for name, value in inputs.items():
        match _path_shape(properties.get(name) or {}), value:
            case "path", str():
                out[name] = _absolute(value, cwd)
            case "list", list():
                out[name] = [_absolute(item, cwd) if isinstance(item, str) else item for item in value]
    return out


def _path_shape(schema: dict[str, Any]) -> str | None:
    """"path" for a path field, "list" for a list of paths (either possibly optional), else None."""
    for option in (schema, *schema.get("anyOf", [])):
        if option.get("format") == "path":
            return "path"
        if option.get("type") == "array" and (option.get("items") or {}).get("format") == "path":
            return "list"
    return None


def _absolute(value: str, cwd: Path) -> str:
    return os.path.normpath(os.path.join(cwd, value)) if value else value

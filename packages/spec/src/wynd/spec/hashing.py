"""Canonical JSON and content hashes, all "sha256:<hex>" (PLAN §3.19; $DRAFTS/01 §8)."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from enum import Enum
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from wynd.spec.schemas import normalize_schema
from wynd.spec.typelang import TList, TObject, TScalar, render_type

if TYPE_CHECKING:
    from wynd.spec.interface import Interface
    from wynd.spec.proto_step import ProtoStep


def jsonable(obj: Any) -> Any:
    """JSON-mode value per $DRAFTS/01 §8 (int keys -> str, dates -> ISO, Path -> POSIX, models dumped)."""
    match obj:
        case None | bool() | str():
            return obj
        case int():
            return obj
        case float():
            if not math.isfinite(obj):
                raise ValueError(f"non-finite float {obj!r} cannot be hashed")
            return obj
        case datetime() | date():
            return obj.isoformat()
        case PurePath():
            return obj.as_posix()
        case Enum():
            return jsonable(obj.value)
        case BaseModel():
            return jsonable(obj.model_dump(mode="json", by_alias=True, exclude_defaults=True))
        case TScalar() | TList() | TObject():
            return jsonable(render_type(obj))
        case Mapping():
            out: dict[str, Any] = {}
            for key, value in obj.items():
                text = jsonable(key) if isinstance(key, (str, int, Enum)) and not isinstance(key, bool) else None
                if not isinstance(text, (str, int)):
                    raise TypeError(f"mapping key {key!r} cannot be hashed")
                text = str(text)
                if text in out:
                    raise TypeError(f"mapping keys collide after conversion to strings: {text!r}")
                out[text] = jsonable(value)
            return out
        case list() | tuple():
            return [jsonable(item) for item in obj]
    raise TypeError(f"{type(obj).__name__} is not JSON-serialisable for hashing")


def canonical_json(obj: Any) -> bytes:
    """Sorted keys, (",", ":") separators, ensure_ascii=False, allow_nan=False, UTF-8."""
    text = json.dumps(jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return text.encode()


def hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_obj(obj: Any) -> str:
    return hash_bytes(canonical_json(obj))


def proto_hash(proto: ProtoStep) -> str:
    """Hash of the normalised proto: formatting, comments, key order and flat-vs-nested outputs do not change it."""
    return hash_obj({"kind": "proto_step", "doc": proto.model_dump(mode="json", by_alias=True, exclude_defaults=True)})


def interface_hash(iface: Interface) -> str:
    """Hash of the normalised input and per-exit output schemas."""
    return hash_obj(
        {
            "input": normalize_schema(iface.input),
            "outputs": {exit: normalize_schema(schema) for exit, schema in iface.outputs.items()},
        }
    )


_REQUIREMENT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(.*)$", re.S)


def normalize_requirement(req: str) -> str:
    """PEP 503 name + whitespace-collapsed rest."""
    text = req.strip()
    match = _REQUIREMENT.match(text)
    if match is None:
        return " ".join(text.split())
    name, rest = match.groups()
    return re.sub(r"[-_.]+", "-", name).lower() + " ".join(rest.split())


def dependency_set_hash(requirements: Iterable[str], python: str | None = None) -> str:
    """Identity of a venv: normalised, deduplicated, sorted requirements plus the interpreter."""
    normalized = sorted({normalize_requirement(r) for r in requirements})
    return hash_obj({"requirements": normalized, "python": python})

"""Canonical JSON and content hashes, all "sha256:<hex>" (PLAN §3.19; $DRAFTS/01 §8)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.spec.interface import Interface
    from wynd.spec.proto_step import ProtoStep


def jsonable(obj: Any) -> Any:
    """JSON-mode value per $DRAFTS/01 §8 (int keys -> str, dates -> ISO, Path -> POSIX, models dumped)."""
    raise NotImplementedError("PLAN §3.19")


def canonical_json(obj: Any) -> bytes:
    """Sorted keys, (",", ":") separators, ensure_ascii=False, allow_nan=False, UTF-8."""
    raise NotImplementedError("PLAN §3.19")


def hash_bytes(data: bytes) -> str:
    raise NotImplementedError("PLAN §3.19")


def hash_obj(obj: Any) -> str:
    raise NotImplementedError("PLAN §3.19")


def proto_hash(proto: ProtoStep) -> str:
    """Hash of the normalised proto: formatting, comments, key order and flat-vs-nested outputs do not change it."""
    raise NotImplementedError("PLAN §3.19")


def interface_hash(iface: Interface) -> str:
    """Hash of the normalised input and per-exit output schemas."""
    raise NotImplementedError("PLAN §3.19")


def normalize_requirement(req: str) -> str:
    """PEP 503 name + whitespace-collapsed rest."""
    raise NotImplementedError("PLAN §3.19")


def dependency_set_hash(requirements: Iterable[str], python: str | None = None) -> str:
    """Identity of a venv: normalised, deduplicated, sorted requirements plus the interpreter."""
    raise NotImplementedError("PLAN §3.19")

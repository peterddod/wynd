"""JSON Schema helpers: normalisation, path walking, nullability (PLAN §4.1; $DRAFTS/01 §7.9, §8)."""

from collections.abc import Sequence
from typing import Any


class _Sentinel:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return self.name


ANY = _Sentinel("ANY")  # schema_at: the path continues into an open object or unknown schema
MISSING = _Sentinel("MISSING")  # schema_at: the path does not exist in the schema


def normalize_schema(schema: dict) -> dict:
    """Inline $ref, drop titles/descriptions/examples/$defs and the exit property's default/type, sort required."""
    raise NotImplementedError("PLAN §3.19")


def schema_at(schema: dict, path: Sequence[str | int | None]) -> Any:
    """The sub-schema at `path`, or ANY, or MISSING."""
    raise NotImplementedError("PLAN §3.5")


def is_nullable(schema: dict) -> bool:
    raise NotImplementedError("PLAN §3.5")


def strip_null(schema: dict) -> dict:
    raise NotImplementedError("PLAN §3.5")

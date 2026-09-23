"""Run, job and other ids (PLAN §3.1): `f"{prefix}_{utc:%Y%m%dT%H%M%S}{ms:03d}_{secrets.token_hex(3)}"`; ids sort by
time."""

from __future__ import annotations


def new_id(prefix: str) -> str:
    raise NotImplementedError("PLAN §3.1")


def valid_id(value: str) -> bool:
    """`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`."""
    raise NotImplementedError("PLAN §3.1")

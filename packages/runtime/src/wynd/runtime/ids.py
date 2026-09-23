"""Run, job and other ids (PLAN §3.1): `f"{prefix}_{utc:%Y%m%dT%H%M%S}{ms:03d}_{secrets.token_hex(3)}"`; ids sort by
time."""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    """E.g. `new_id("run") == "run_20260922T215001100_a1b2c3"`; prefixes `run job rel chat fire turn it`."""
    now = _utc_now()
    return f"{prefix}_{now:%Y%m%dT%H%M%S}{now.microsecond // 1000:03d}_{secrets.token_hex(3)}"


def valid_id(value: str) -> bool:
    """`^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`: filesystem- and URL-safe, so externally supplied ids can name files."""
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None

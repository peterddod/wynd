"""M3 type checks over the typed sites (PLAN §6.1; owner PROC-TYPES, M3).

`infer_type` per site (or `site.src_schema` for synthetic sites), then `check_assignable(src, site.dst_schema)` for
`with:` -> target Input, `$exit` -> process outputs, entry inputs, on_error Input (`ProcessError` schema) and handler
exits -> `E-TYPE-ASSIGN`/`W-TYPE-ASSIGN`/`W-TYPE-NULL`/`E-TYPE-OP`. Until M3 this hook reports nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.spec.errors import Diagnostic

    from ..loader import LoadedProcess
    from .exprcheck import TypedSite


def check_types(lp: LoadedProcess, sites: Sequence[TypedSite]) -> list[Diagnostic]:
    return []

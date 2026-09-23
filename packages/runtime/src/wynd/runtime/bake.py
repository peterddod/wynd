"""Single-venv run of a baked process (`$DRAFTS/04 §8.9`): called by the zipapp bootstrap with the extracted
`site`; every worker is this interpreter."""

from __future__ import annotations

from pathlib import Path


def main(plan_path: Path, site: Path, argv: list[str]) -> int:
    raise NotImplementedError("PLAN §5.1")

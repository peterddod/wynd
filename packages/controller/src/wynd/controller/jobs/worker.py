"""The job worker process (PLAN §8.1; `$DRAFTS/06 §6.5`). Stub; CTL-JOBS.

`python -m wynd.controller.jobs.worker --workspace P --job ID [--checkout worktree|clone] [--remote URL]
[--phase all|prepare|finalize]`: SIGTERM -> `JobCancelled`, load `.env`, open the stores, run `execute_job`, exit 0
(70 only for infrastructure errors before the record can be updated).
"""

from __future__ import annotations

from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    raise NotImplementedError("PLAN §8.1")


if __name__ == "__main__":
    raise SystemExit(main())

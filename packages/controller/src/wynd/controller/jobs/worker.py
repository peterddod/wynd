"""The job worker process (PLAN §8.1; `$DRAFTS/06 §6.5`).

`python -m wynd.controller.jobs.worker --workspace P --job ID [--checkout worktree|clone] [--remote URL]
[--phase all|prepare|finalize]`: SIGTERM -> `JobCancelled`, load `.env`, open the stores, run `execute_job`, exit 0
(70 only for infrastructure errors before the record can be updated). `P` is the workspace root; with
`--checkout clone` it is the workspace inside the clone (the pod's `/work/repo/<subdir>`), and the clone of `URL` is
made there unless a checkout init container already made it. Afterwards the worker adds its CPU time (its own and its
children's) to the record's `cpu_ms`.
"""

from __future__ import annotations

import argparse
import resource
import signal
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from wynd.controller.jobs import records
from wynd.controller.jobs.checkout import CheckoutBackend, CloneCheckout, WorktreeCheckout
from wynd.controller.jobs.harness import JobCancelled, execute_job
from wynd.runtime.storage import stores_from_env

EXIT_INFRA = 70


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    signal.signal(signal.SIGTERM, _cancel)
    try:
        root = Path(args.workspace).absolute()
        if (root / ".env").is_file():
            from wynd.controller.envfile import load_into_environ

            load_into_environ(root)
        stores = stores_from_env(data_dir=root / ".wynd")          # this process's environment
        record = records.load(stores.runs, args.job)
        checkout = _checkout(args, root, record.workspace_rel)
    except Exception:
        traceback.print_exc()
        return EXIT_INFRA
    execute_job(args.job, phase=args.phase, checkout=checkout, stores=stores, registry=stores.registry)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    records.update(stores.runs, args.job, cpu_ms=(record.cpu_ms or 0) + _cpu_ms())
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m wynd.controller.jobs.worker", description="Run one Wynd job.")
    p.add_argument("--workspace", required=True, help="workspace root")
    p.add_argument("--job", required=True, help="job id")
    p.add_argument("--checkout", choices=("worktree", "clone"), default="worktree")
    p.add_argument("--remote", help="git remote to clone from and push result branches to (--checkout clone)")
    p.add_argument("--phase", choices=("all", "prepare", "finalize"), default="all")
    return p


def _checkout(args: argparse.Namespace, root: Path, workspace_rel: str) -> CheckoutBackend:
    if args.checkout == "worktree":
        return WorktreeCheckout(root, root / ".wynd")
    if not args.remote:
        raise ValueError("--checkout clone needs --remote")
    depth = len(PurePosixPath(workspace_rel).parts) if workspace_rel else 0
    workdir = root.parents[depth - 1] if depth else root
    return CloneCheckout(args.remote, workdir, workspace_rel)


def _cancel(signum: int, frame: object) -> None:
    raise JobCancelled(f"received signal {signum}")


def _cpu_ms() -> int:
    total = 0.0
    for who in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN):
        usage = resource.getrusage(who)
        total += usage.ru_utime + usage.ru_stime
    return int(total * 1000)


if __name__ == "__main__":
    sys.exit(main())

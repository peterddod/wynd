"""Tree-based hashes (PLAN §3.19; owner PROC-WS).

`tree_hash` = sha256 over sorted `"<rel>\\0<blob id>\\n"`; `step_hash(tree, pkg) = tree_hash(pkg.dir)`;
`process_hash` includes every child's hash. Canonical JSON and object hashing come from `wynd.spec.hashing`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .loader import LoadedProcess, StepPackage
    from .workspace import Tree


def tree_hash(tree: Tree, dir: str) -> str:
    raise NotImplementedError("PLAN §3.19 tree_hash")


def step_hash(tree: Tree, pkg: StepPackage) -> str:
    raise NotImplementedError("PLAN §3.19 step_hash")


def process_hash(tree: Tree, lp: LoadedProcess) -> str:
    raise NotImplementedError("PLAN §3.19 process_hash")

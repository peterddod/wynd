"""Tree-based hashes (PLAN §3.19; owner PROC-WS).

`tree_hash` = sha256 over sorted `"<rel>\\0<blob id>\\n"`; `step_hash(tree, pkg) = tree_hash(pkg.dir)`;
`process_hash` includes every child's hash. Canonical JSON and object hashing come from `wynd.spec.hashing`.
Blob ids are git's, so a `WorkingTree` and a `CommitTree` of the same content hash equally.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from wynd.spec.hashing import hash_obj

if TYPE_CHECKING:
    from .loader import LoadedProcess, StepPackage
    from .workspace import Tree


def tree_hash(tree: Tree, dir: str) -> str:
    """Hash of every file under workspace-relative `dir` ("" = the whole tree): paths relative to `dir`, blob ids."""
    prefix = f"{dir.rstrip('/')}/" if dir else ""
    digest = hashlib.sha256()
    for path in sorted(f for f in tree.files() if f.startswith(prefix)):
        digest.update(f"{path[len(prefix):]}\0{tree.blob_id(path)}\n".encode())
    return "sha256:" + digest.hexdigest()


def step_hash(tree: Tree, pkg: StepPackage) -> str:
    return tree_hash(tree, pkg.dir)


def process_hash(tree: Tree, lp: LoadedProcess) -> str:
    """hash_obj of the process dir hash, each root-step binding's `{id, hash}` and every child's process_hash."""
    return _process_hash(tree, lp, {})


def _process_hash(tree: Tree, lp: LoadedProcess, memo: dict[str, str]) -> str:
    if lp.id not in memo:
        memo[lp.id] = hash_obj({
            "dir": tree_hash(tree, lp.dir),
            "steps": {
                name: {"id": rs.package.id, "hash": tree_hash(tree, rs.package.dir)}
                for name, rs in lp.steps.items()
                if rs.ref_kind == "root" and rs.package is not None
            },
            "children": {cid: _process_hash(tree, child, memo) for cid, child in lp.children.items()},
        })
    return memo[lp.id]

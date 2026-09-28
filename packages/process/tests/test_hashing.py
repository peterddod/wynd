"""Tree, step and process hashes (PLAN §3.19): equal from the working tree and a commit; parents include children."""

import hashlib

from wynd.process.hashing import process_hash, step_hash, tree_hash
from wynd.process.workspace import CommitTree, WorkingTree, load_workspace
from wynd.spec.hashing import hash_obj

READ_DIR = "processes/intake/steps/read"


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def hashes(ws, tree=None) -> dict[str, str]:
    """process_hash of every process, and step_hash of every package, loaded through `tree`."""
    workspace = load_workspace(ws, tree)
    out = {}
    for pid in workspace.process_ids():
        lp = workspace.load_process(pid)
        out[pid] = process_hash(workspace.tree, lp)
        out.update({sid: step_hash(workspace.tree, pkg) for sid, pkg in lp.closure_packages().items()})
    return out


def test_tree_hash_is_sha256_over_sorted_relative_paths_and_blob_ids(make_repo):
    files = {"pkg/b.py": b"print('b')\n", "pkg/a.py": b"", "pkg/sub/c.json": b"{}\n", "pkg.txt": b"not in pkg\n"}
    ws = make_repo(files={"wynd.yaml": "", **files})
    expected = hashlib.sha256()
    for rel in ("a.py", "b.py", "sub/c.json"):
        expected.update(f"{rel}\0{git_blob(files['pkg/' + rel])}\n".encode())
    for tree in (WorkingTree(ws), CommitTree(ws, "HEAD")):
        assert tree_hash(tree, "pkg") == tree_hash(tree, "pkg/") == "sha256:" + expected.hexdigest()
        assert tree_hash(tree, "missing") == "sha256:" + hashlib.sha256().hexdigest()
    whole = hashlib.sha256()
    for rel in sorted([*files, "wynd.yaml"]):
        whole.update(f"{rel}\0{git_blob(files.get(rel, b''))}\n".encode())
    assert tree_hash(WorkingTree(ws), "") == "sha256:" + whole.hexdigest()


def test_hashes_agree_between_working_tree_and_commit(make_repo):
    ws = make_repo("basic")
    from_disk, from_commit = hashes(ws), hashes(ws, CommitTree(ws, "HEAD"))
    assert from_disk == from_commit
    assert set(from_disk) == {
        "intake", "finance/invoices", "intake#read", "intake#draft", "intake#guess", "finance:extract/invoice",
        "shared:greet", "finance/invoices#check",
    }
    assert all(value.startswith("sha256:") and len(value) == 71 for value in from_disk.values())


def test_step_hash_follows_package_content_but_not_ignored_files(make_repo, write_files, commit):
    ws = make_repo("basic", files={".gitignore": "__pycache__/\n"})
    before = hashes(ws)["intake#read"]
    write_files(ws, {f"{READ_DIR}/__pycache__/read.cpython-312.pyc": b"\x00bytecode"})
    assert hashes(ws)["intake#read"] == before
    write_files(ws, {f"{READ_DIR}/cassettes/abc.json": '{"response": 1}'})
    with_cassette = hashes(ws)["intake#read"]
    assert with_cassette != before
    commit(ws, "record a cassette")
    assert hashes(ws, CommitTree(ws, "HEAD"))["intake#read"] == with_cassette
    write_files(ws, {f"{READ_DIR}/cassettes/abc.json": '{"response": 2}'})
    assert hashes(ws)["intake#read"] not in (before, with_cassette)


def test_process_hash_formula(make_repo):
    workspace = load_workspace(make_repo("basic"))
    lp, tree = workspace.load_process("intake"), workspace.tree
    child = lp.children["finance/invoices"]
    assert process_hash(tree, child) == hash_obj({
        "dir": tree_hash(tree, "processes/finance/invoices"), "steps": {}, "children": {},
    })
    assert process_hash(tree, lp) == hash_obj({
        "dir": tree_hash(tree, "processes/intake"),
        "steps": {
            "extract": {
                "id": "finance:extract/invoice", "hash": tree_hash(tree, "teams/finance/steps/extract/invoice"),
            },
            "greet": {"id": "shared:greet", "hash": tree_hash(tree, "shared/steps/greet")},
        },
        "children": {"finance/invoices": process_hash(tree, child)},
    })


def test_parent_hash_includes_children_and_root_steps(make_repo, write_files):
    ws = make_repo("basic")
    before = hashes(ws)

    write_files(ws, {"processes/finance/invoices/proto/check.yaml": "# edited child\n" + (
        ws / "processes/finance/invoices/proto/check.yaml").read_text()})
    after_child = hashes(ws)
    assert after_child["finance/invoices"] != before["finance/invoices"]
    assert after_child["intake"] != before["intake"]
    assert after_child["intake#read"] == before["intake#read"]

    write_files(ws, {"teams/finance/steps/extract/invoice/invoice.py": "# edited root step\n"})
    after_root = hashes(ws)
    assert after_root["finance:extract/invoice"] != after_child["finance:extract/invoice"]
    assert after_root["intake"] != after_child["intake"]
    assert after_root["finance/invoices"] == after_child["finance/invoices"]


def test_unrelated_changes_do_not_move_a_process_hash(make_repo, write_files):
    ws = make_repo("basic")
    before = hashes(ws)
    write_files(ws, {
        "processes/other/process.yaml": "kind: process\nname: other\nentry: s\nsteps:\n  s: {use: ./steps/s}\n",
        "README.md": "workspace notes",
        ".wynd/traces/run_1.jsonl": "{}",
    })
    after = hashes(ws)
    assert {key: after[key] for key in before} == before


def test_diamond_child_is_hashed_consistently(make_repo, write_files):
    ws = make_repo("graph")
    workspace = load_workspace(ws)
    top = workspace.load_process("diamond/top")
    left, right = top.children["diamond/left"], top.children["diamond/right"]
    assert process_hash(workspace.tree, left) != process_hash(workspace.tree, right)  # different dirs
    before = process_hash(workspace.tree, top)
    write_files(ws, {"processes/diamond/bottom/proto/work.yaml": "# edited\n" + (
        ws / "processes/diamond/bottom/proto/work.yaml").read_text()})
    assert hashes(ws)["diamond/top"] != before

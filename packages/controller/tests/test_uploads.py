"""`UploadService` (PLAN §3.21 amendment 7; `$DRAFTS/06 §5.10`): `.wynd/uploads/<sha256>/<basename>`."""

from __future__ import annotations

import hashlib

import pytest

from wynd.controller.errors import Invalid


def test_put_stores_the_file_under_its_content_hash(controller):
    data = b"%PDF-1.4 invoice"
    path = controller.uploads.put("acme_inv_1042.pdf", data)
    uploads = controller.ctx.state_dir / "uploads"
    assert path == (uploads / hashlib.sha256(data).hexdigest() / "acme_inv_1042.pdf").absolute()
    assert path.is_absolute() and path.read_bytes() == data
    assert [p.name for p in path.parent.iterdir()] == ["acme_inv_1042.pdf"]     # no temp file left behind


@pytest.mark.parametrize("filename", ["C:\\Users\\me\\inv.pdf", "a/b/inv.pdf", "/abs/inv.pdf", "../inv.pdf"])
def test_only_the_basename_is_kept(controller, filename):
    path = controller.uploads.put(filename, b"x")
    assert path.name == "inv.pdf" and path.parent.parent == (controller.ctx.state_dir / "uploads").absolute()


def test_same_content_and_name_is_stored_once(controller):
    first = controller.uploads.put("inv.pdf", b"one")
    first_mtime = first.stat().st_mtime_ns
    assert controller.uploads.put("inv.pdf", b"one") == first and first.stat().st_mtime_ns == first_mtime
    other = controller.uploads.put("inv.pdf", b"two")
    renamed = controller.uploads.put("copy.pdf", b"one")
    assert other.parent != first.parent and other.read_bytes() == b"two" and first.read_bytes() == b"one"
    assert renamed.parent == first.parent and renamed.name == "copy.pdf"


@pytest.mark.parametrize("filename", ["", "/", ".", "..", "a/..", "bad\0name"])
def test_put_refuses_names_that_are_not_file_names(controller, filename):
    with pytest.raises(Invalid) as err:
        controller.uploads.put(filename, b"x")
    assert (err.value.http, err.value.code) == (422, "invalid")
    assert not (controller.ctx.state_dir / "uploads").exists() or not any(
        (controller.ctx.state_dir / "uploads").iterdir())

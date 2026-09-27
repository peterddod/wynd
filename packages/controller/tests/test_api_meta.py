"""Meta routes (PLAN §3.21 amendments 7, 12; `$DRAFTS/07 §12.1` routes 1, 2, 33): health, meta and uploads."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from support.ctl_api_client import api_client
from wynd.controller.api import routes_meta
from wynd.runtime.providers import check_provider


@pytest.fixture
def client(controller):
    return api_client(controller)


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_meta_describes_the_workspace_and_the_chat_provider(client, workspace, git):
    meta = client.get("/api/meta").json()
    assert meta["default_provider"] == "claude-code" and meta["latency"] == ["fast", "normal"]
    assert meta["edge_kinds"] == ["deterministic", "agentic"] and meta["default_max_traversals"] == 10
    assert meta["workspace"] == {"root": str(workspace), "branch": "main",
                                 "head": git(workspace, "rev-parse", "HEAD").strip(),
                                 "process_roots": ["processes"], "step_roots": {"shared": "shared/steps"}}
    ready, message = check_provider("claude-code")
    assert meta["llm"] == {"provider": "claude-code", "ready": ready, "message": message}


def test_an_upload_is_stored_by_content_hash(client, workspace):
    data = b"%PDF-1.4 invoice"
    response = client.post("/api/uploads", content=data, headers={"X-Wynd-Filename": "inv 1042.pdf",
                                                                  "Content-Type": "application/pdf"})
    assert response.status_code == 201
    path = Path(response.json()["path"])
    assert path == workspace / ".wynd" / "uploads" / hashlib.sha256(data).hexdigest() / "inv 1042.pdf"
    assert path.read_bytes() == data
    again = client.post("/api/uploads", content=data, headers={"X-Wynd-Filename": "inv 1042.pdf"})
    assert again.json() == response.json()


def test_an_upload_name_may_be_percent_encoded_and_never_escapes(client, workspace):
    response = client.post("/api/uploads", content=b"x", headers={"X-Wynd-Filename": "fa%C3%A7ade.txt"})
    assert Path(response.json()["path"]).name == "façade.txt"
    response = client.post("/api/uploads", content=b"y", headers={"X-Wynd-Filename": "../../etc/passwd"})
    path = Path(response.json()["path"])
    assert path.name == "passwd" and path.parent.parent == workspace / ".wynd" / "uploads"


def test_an_upload_needs_a_file_name(client):
    response = client.post("/api/uploads", content=b"x")
    assert (response.status_code, response.json()["error"]["code"]) == (422, "invalid")
    assert "X-Wynd-Filename" in response.json()["error"]["hint"]


def test_an_upload_over_the_cap_is_413(client, workspace, monkeypatch):
    monkeypatch.setattr(routes_meta, "MAX_UPLOAD_BYTES", 8)
    response = client.post("/api/uploads", content=b"123456789", headers={"X-Wynd-Filename": "big.bin"})
    assert (response.status_code, response.json()["error"]["code"]) == (413, "too_large")

    def chunks():                                        # no Content-Length: the stream itself is counted
        yield b"12345"
        yield b"6789"

    response = client.post("/api/uploads", content=chunks(), headers={"X-Wynd-Filename": "big.bin"})
    assert (response.status_code, response.json()["error"]["code"]) == (413, "too_large")
    assert not (workspace / ".wynd" / "uploads").exists() or not any((workspace / ".wynd" / "uploads").iterdir())
    assert client.post("/api/uploads", content=b"12345678", headers={"X-Wynd-Filename": "ok.bin"}).status_code == 201

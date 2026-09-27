"""The web bundle mount (PLAN §3.21; `$DRAFTS/06 §8.3` "Static web", `$DRAFTS/07 §3.5`): where the bundle is found,
that it is served at `/` after every API route, is public, and that a missing bundle answers a hint page."""

from __future__ import annotations

from pathlib import Path

import pytest

from support.ctl_api_client import api_client
from wynd.controller.api import static


def bundle(root: Path, title: str) -> Path:
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(f"<!doctype html><title>{title}</title><div id=root></div>")
    (root / "assets" / "app.js").write_text("console.log('wynd');")
    return root


@pytest.fixture
def packaged(tmp_path, monkeypatch) -> Path:
    """The packaged `web_dist` stand-in (the real one exists only after `npm run build`)."""
    path = tmp_path / "packaged"
    monkeypatch.setattr(static, "PACKAGED", path)
    return path


def test_the_packaged_bundle_is_the_real_web_dist_of_the_wheel():
    import wynd.controller

    assert static.PACKAGED == Path(wynd.controller.__file__).resolve().parent / "web_dist"


def test_web_dist_dir_takes_the_first_directory_with_an_index(tmp_path, packaged):
    explicit = bundle(tmp_path / "explicit", "explicit")
    assert static.web_dist_dir(explicit) == explicit
    assert static.web_dist_dir(None) is None
    bundle(packaged, "packaged")
    assert static.web_dist_dir(tmp_path / "empty") == packaged
    assert static.web_dist_dir(None) == packaged
    assert static.web_dist_dir(explicit) == explicit


def test_the_bundle_is_served_at_the_root(controller, tmp_path, packaged):
    client = api_client(controller, web_dist=bundle(tmp_path / "dist", "wynd web"))
    index = client.get("/")
    assert index.status_code == 200 and "<title>wynd web</title>" in index.text
    asset = client.get("/assets/app.js")
    assert asset.status_code == 200 and asset.text == "console.log('wynd');"
    assert client.get("/assets/missing.js").status_code == 404


def test_api_routes_win_over_the_mount(controller, tmp_path, packaged):
    client = api_client(controller, web_dist=bundle(tmp_path / "dist", "wynd web"))
    assert client.get("/api/health").json() == {"ok": True}
    missing = client.get("/api/nope")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    assert client.post("/hooks/releases/rel_nope", json={}).json()["error"]["code"] == "not_found"


def test_the_bundle_is_public_when_the_api_needs_a_token(controller, tmp_path, packaged):
    client = api_client(controller, web_dist=bundle(tmp_path / "dist", "wynd web"), api_token="s3cret")
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/meta").status_code == 401


def test_wynd_web_dist_from_the_controller_env(workspace, make_controller, tmp_path, packaged):
    dist = bundle(tmp_path / "from-env", "from env")
    ctl = make_controller(workspace, env={"WYND_WEB_DIST": str(dist)})
    assert "<title>from env</title>" in api_client(ctl).get("/").text
    explicit = bundle(tmp_path / "explicit", "explicit")
    assert "<title>explicit</title>" in api_client(ctl, web_dist=explicit).get("/").text


def test_the_packaged_bundle_is_the_fallback(controller, packaged):
    bundle(packaged, "packaged")
    assert "<title>packaged</title>" in api_client(controller).get("/").text


def test_without_a_bundle_the_root_explains_how_to_build_it(controller, packaged):
    response = api_client(controller).get("/")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert "Web UI not built" in response.text
    assert "npm --prefix packages/web ci &amp;&amp; npm --prefix packages/web run build" in response.text
    assert api_client(controller).get("/assets/app.js").status_code == 404

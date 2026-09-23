"""`HttpClient` against a local ThreadingHTTPServer (offline; `$DRAFTS/02 §3.6`)."""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from wynd.runtime import __version__
from wynd.runtime.http import HttpClient, HttpError, HttpResponse


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Echo-Agent", self.headers.get("User-Agent", ""))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        match url.path:
            case "/missing":
                self._reply(404, b'{"error": "not found"}')
            case "/slow":
                time.sleep(1.0)
                self._reply(200, b"{}")
            case "/latin":
                self._reply(200, "café".encode("latin-1"), "text/plain; charset=latin-1")
            case _:
                self._reply(200, json.dumps({"path": url.path, "query": parse_qs(url.query)}).encode())

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self._reply(201, json.dumps({"body": body.decode(), "type": self.headers["Content-Type"]}).encode())


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def test_get_with_params_and_default_user_agent(server):
    response = HttpClient().get(f"{server}/items", params={"q": "a b", "n": "2"})
    assert isinstance(response, HttpResponse)
    assert response.status == 200
    assert response.json() == {"path": "/items", "query": {"q": ["a b"], "n": ["2"]}}
    assert response.headers["x-echo-agent"] == f"wynd-runtime/{__version__}"
    assert response.headers["content-type"] == "application/json"
    assert response.raise_for_status() is response


def test_params_extend_an_existing_query(server):
    response = HttpClient().get(f"{server}/items?a=1", params={"b": "2"})
    assert response.json()["query"] == {"a": ["1"], "b": ["2"]}


def test_post_json_and_raw_data(server):
    client = HttpClient(user_agent="probe/1")
    sent = client.post(f"{server}/save", json={"total": 1200.5, "name": "café"})
    assert sent.status == 201
    assert json.loads(sent.json()["body"]) == {"total": 1200.5, "name": "café"}
    assert sent.json()["type"] == "application/json"
    assert sent.headers["x-echo-agent"] == "probe/1"
    raw = client.request("post", f"{server}/save", data=b"a=1", headers={"Content-Type": "text/plain"})
    assert raw.json() == {"body": "a=1", "type": "text/plain"}


def test_error_statuses_are_returned_not_raised(server):
    response = HttpClient().get(f"{server}/missing")
    assert response.status == 404
    assert response.json() == {"error": "not found"}
    with pytest.raises(HttpError, match="HTTP 404") as err:
        response.raise_for_status()
    assert err.value.response is response


def test_text_uses_the_declared_charset(server):
    assert HttpClient().get(f"{server}/latin").text() == "café"
    assert HttpResponse("u", 200, {}, "naïve".encode()).text() == "naïve"


def test_timeouts_raise_http_error_without_a_response(server):
    with pytest.raises(HttpError) as err:
        HttpClient(timeout=5).get(f"{server}/slow", timeout=0.2)
    assert err.value.response is None
    assert "GET" in str(err.value)


def test_connection_failures_raise_http_error_without_a_response():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(HttpError) as err:
        HttpClient(timeout=2).get(f"http://127.0.0.1:{port}/")
    assert err.value.response is None

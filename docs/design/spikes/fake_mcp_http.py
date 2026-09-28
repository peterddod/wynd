"""Stdlib-only MCP server over streamable HTTP (JSON responses, no SSE).

Prototype of wynd.runtime.testing.fake_mcp. Requires `Authorization: Bearer <token>`.
Usage: python fake_mcp_http.py <port> <token>
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOOLS = [
    {
        "name": "get_issue",
        "description": "Get a GitHub issue by number.",
        "inputSchema": {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]},
    },
    {
        "name": "delete_repo",
        "description": "Delete a repository (must never be allowed).",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
    },
]
LOG = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        auth = self.headers.get("Authorization", "")
        if auth != "Bearer " + TOKEN:
            self.send_response(401)
            self.end_headers()
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        LOG.append(body.get("method"))
        sys.stderr.write("MCP %s %s\n" % (body.get("method"), json.dumps(body.get("params", {}))[:120]))
        if "id" not in body:  # notification
            self.send_response(202)
            self.end_headers()
            return
        m = body["method"]
        if m == "initialize":
            result = {
                "protocolVersion": body["params"].get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fake-github", "version": "0.0.1"},
            }
        elif m == "tools/list":
            result = {"tools": TOOLS}
        elif m == "tools/call":
            p = body["params"]
            if p["name"] == "get_issue":
                result = {"content": [{"type": "text", "text": json.dumps({"number": p["arguments"]["number"], "title": "Login page broken", "state": "open"})}], "isError": False}
            else:
                result = {"content": [{"type": "text", "text": "forbidden"}], "isError": True}
        elif m == "ping":
            result = {}
        else:
            out = {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32601, "message": "method not found"}}
            return self._json(out)
        self._json({"jsonrpc": "2.0", "id": body["id"], "result": result}, session=(m == "initialize"))

    def do_GET(self):  # no server-initiated SSE stream
        self.send_response(405)
        self.end_headers()

    def do_DELETE(self):
        self.send_response(200)
        self.end_headers()

    def _json(self, obj, session=False):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if session:
            self.send_header("Mcp-Session-Id", "sess-1")
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port, TOKEN = int(sys.argv[1]), sys.argv[2]
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()

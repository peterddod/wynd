"""A stdlib fake MCP server for tests and offline demos: HTTP (JSON and SSE bodies) and stdio
(`python -m wynd.runtime.mcp.fake_server`; spike `docs/design/spikes/fake_mcp_http.py`)."""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    raise NotImplementedError("PLAN §5.1")


if __name__ == "__main__":
    raise SystemExit(main())

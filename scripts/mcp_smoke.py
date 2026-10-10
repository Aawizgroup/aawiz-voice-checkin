"""Tiny real MCP client: connects over Streamable HTTP with a bearer token and calls a tool.

Usage: python scripts/mcp_smoke.py [url] [token]
Prints the negotiated protocol version, the tool names and one tools/call result.
Exits non-zero on failure.
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp_types.version import is_version_at_least


async def run(url: str, token: str) -> int:
    headers = {"Authorization": f"Bearer {token}"}
    async with create_mcp_http_client(headers=headers) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            version = client.protocol_version
            tools = sorted(t.name for t in (await client.list_tools()).tools)
            result = await client.call_tool("get_privacy_summary", {})
            print(f"protocol_version={version}")
            print(f"tools={','.join(tools)}")
            print(f"call_ok={not result.is_error}")
            ok = (
                is_version_at_least(version, "2025-11-25")
                and len(tools) == 7
                and not result.is_error
            )
            return 0 if ok else 1


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")
    token = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("SIM_TOKEN", "")
    sys.exit(asyncio.run(run(url, token)))


if __name__ == "__main__":
    main()

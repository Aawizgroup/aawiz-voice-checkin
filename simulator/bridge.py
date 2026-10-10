"""The simulator's MCP client: Streamable HTTP with a bearer token, one call per request."""

from __future__ import annotations

import json
from dataclasses import dataclass

from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client


@dataclass
class ToolCall:
    tool: str
    ok: bool
    data: dict | None
    text: str
    summary: str


def summarise(tool: str, ok: bool, data: dict | None) -> str:
    """A few words for the MCP activity panel."""
    if not ok or data is None:
        return "rejected"
    if data.get("safety"):
        return "safety message"
    if tool == "set_consent":
        on = {True: "on", False: "off"}
        return f"scores {on[data['scores']]}, notes {on[data['notes']]}"
    if tool == "log_checkin":
        return "stored" if data.get("stored") else "not stored"
    if tool == "get_weekly_reflection":
        if not data.get("enough_data"):
            return f"{data['data_points']} check-ins, not enough yet"
        d = data["direction"]
        return f"{data['data_points']} check-ins; mood {d['mood']}, energy {d['energy']}, stress {d['stress']}"
    if tool == "suggest_micro_action":
        return str(data.get("action"))
    if tool == "get_privacy_summary":
        return f"retention {data['retention_days']} days"
    if tool == "export_my_data":
        return f"{len(data['data']['checkins'])} check-ins exported"
    if tool == "delete_my_data":
        return f"{data['deleted_rows']} rows deleted"
    return "ok"


class McpBridge:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token

    def _client(self, http):
        return Client(streamable_http_client(self.url, http_client=http))

    def _http(self):
        return create_mcp_http_client(headers={"Authorization": f"Bearer {self.token}"})

    async def list_tools(self):
        async with self._http() as http, self._client(http) as client:
            return (await client.list_tools()).tools

    async def call(self, tool: str, args: dict | None = None) -> ToolCall:
        async with self._http() as http, self._client(http) as client:
            result = await client.call_tool(tool, args or {})
        text = "".join(getattr(block, "text", "") for block in result.content)
        data = None
        if isinstance(result.structured_content, dict):
            data = result.structured_content
        elif not result.is_error:
            try:
                data = json.loads(text)
            except ValueError:
                data = None
        ok = not result.is_error
        return ToolCall(tool, ok, data, text, summarise(tool, ok, data))

"""The server speaks MCP 2025-11-25 or later over Streamable HTTP, with exactly 7 tools."""

import pytest
from mcp_types.version import is_version_at_least

from tests.conftest import mcp_client

EXPECTED_TOOLS = {
    "set_consent",
    "log_checkin",
    "get_weekly_reflection",
    "suggest_micro_action",
    "get_privacy_summary",
    "export_my_data",
    "delete_my_data",
}


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_negotiated_protocol_is_at_least_2025_11_25(live_server, mode):
    async with mcp_client(live_server["url"], "token-a", mode=mode) as client:
        version = client.protocol_version
        assert is_version_at_least(version, "2025-11-25"), version


@pytest.mark.anyio
async def test_tools_list_returns_exactly_the_seven_tools(live_server):
    async with mcp_client(live_server["url"], "token-a") as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert len(tools) == 7


@pytest.mark.anyio
async def test_every_tool_has_a_valid_object_schema_and_description(live_server):
    async with mcp_client(live_server["url"], "token-a") as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    for name, tool in tools.items():
        schema = tool.input_schema
        assert schema["type"] == "object", name
        assert isinstance(schema.get("properties", {}), dict), name
        assert tool.description and len(tool.description) > 20, name
        for prop in schema.get("properties", {}).values():
            assert prop.get("description"), f"{name}: every argument is described"

    checkin = tools["log_checkin"].input_schema
    assert set(checkin["required"]) == {"mood", "energy", "stress"}
    for score in ("mood", "energy", "stress"):
        assert checkin["properties"][score]["type"] == "integer"
        assert checkin["properties"][score]["minimum"] == 1
        assert checkin["properties"][score]["maximum"] == 5
    assert set(tools["set_consent"].input_schema["required"]) == {"scores", "notes"}

"""Bearer auth: missing or invalid tokens get 401, a valid token works."""

import httpx2
import pytest

from tests.conftest import mcp_client

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "auth-test", "version": "1"},
    },
}
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


def test_missing_token_returns_401(live_server):
    r = httpx2.post(f"{live_server['url']}/mcp", json=INIT, headers=HEADERS)
    assert r.status_code == 401


def test_invalid_token_returns_401(live_server):
    headers = {**HEADERS, "Authorization": "Bearer not-a-real-token"}
    r = httpx2.post(f"{live_server['url']}/mcp", json=INIT, headers=headers)
    assert r.status_code == 401


def test_health_needs_no_token(live_server):
    r = httpx2.get(f"{live_server['url']}/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


@pytest.mark.anyio
async def test_valid_token_works(live_server):
    async with mcp_client(live_server["url"], "token-a") as client:
        result = await client.call_tool("get_privacy_summary", {})
    assert not result.is_error

from __future__ import annotations

import json
import os
import socket
import threading
import time
from contextlib import asynccontextmanager

import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from server.app import create_app
from server.config import Settings

TOKENS = {"token-a": "user_a", "token-b": "user_b"}

# Tests never take these from the developer's .env: no real ElevenLabs calls, and local mode
# unless BACKEND_MODE=aawiz is exported on purpose (for the production end-to-end test).
os.environ.setdefault("ELEVENLABS_API_KEY", "")
os.environ.setdefault("BACKEND_MODE", "local")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerThread:
    def __init__(self, app, port: int):
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        deadline = time.time() + 15
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError("server did not start")
            time.sleep(0.05)

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(tokens=TOKENS, data_dir=tmp_path, region="NL")


@pytest.fixture
def live_server(settings):
    port = free_port()
    app = create_app(settings, port=port)
    thread = ServerThread(app, port)
    thread.start()
    yield {"url": f"http://127.0.0.1:{port}", "store": app.state.store}
    thread.stop()


@asynccontextmanager
async def mcp_client(base_url: str, token: str, mode: str = "auto"):
    headers = {"Authorization": f"Bearer {token}"}
    async with create_mcp_http_client(headers=headers) as http:
        transport = streamable_http_client(f"{base_url}/mcp", http_client=http)
        async with Client(transport, mode=mode) as client:
            yield client


def payload(result) -> dict:
    """The JSON object a tool returned."""
    if isinstance(result.structured_content, dict):
        return result.structured_content
    return json.loads(result.content[0].text)

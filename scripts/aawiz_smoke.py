"""Smoke test against production Aawiz with the demo session. Not run in CI.

Run `make aawiz-login` once, then `make aawiz-smoke`. Every tool call goes through the real MCP
server (aawiz mode, started here) over Streamable HTTP, then to the Aawiz API.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx2 as httpx  # noqa: E402
import uvicorn  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from mcp.shared._httpx_utils import create_mcp_http_client  # noqa: E402

from server.aawiz_client import AawizClient, AawizError, load_session  # noqa: E402
from server.app import create_app  # noqa: E402
from server.config import get_settings  # noqa: E402
from simulator.tts import from_env  # noqa: E402

TOKEN = "smoke-token"
results: list[bool] = []


def report(ok: bool, name: str, detail: str = "") -> None:
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def call(url: str, tool: str, args: dict) -> dict:
    async with create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            result = await client.call_tool(tool, args)
    text = "".join(getattr(b, "text", "") for b in result.content)
    if result.is_error:
        raise RuntimeError(f"{tool} returned an error: {text[:300]}")
    if isinstance(result.structured_content, dict):
        return result.structured_content
    return json.loads(text)


def main() -> int:
    settings = get_settings()
    if not load_session(settings.aawiz_session_file):
        print(f"No session at {settings.aawiz_session_file}. Run make aawiz-login first.")
        return 1

    # 1. Aawiz API health
    try:
        r = httpx.get(settings.aawiz_api_base.rstrip("/") + "/health/", timeout=10)
        report(r.status_code == 200, "Aawiz API /health", f"status {r.status_code}")
    except httpx.HTTPError as exc:
        report(False, "Aawiz API /health", type(exc).__name__)

    # 2. token refresh (rotates the refresh token and saves the new one)
    client = AawizClient(settings.aawiz_api_base, settings.aawiz_session_file)
    try:
        client.refresh()
        left = round(client._access_exp - time.time())
        report(True, "token refresh", f"access token valid for about {left} s")
    except AawizError as exc:
        report(False, "token refresh", exc.speech)
        return finish()

    # 3-6. the tools, through a real MCP server in aawiz mode. The local mirror is a temp folder.
    with tempfile.TemporaryDirectory() as tmp:
        local = replace(
            settings, tokens={TOKEN: "user_demo"}, data_dir=Path(tmp), backend_mode="aawiz"
        )
        port = free_port()
        app = create_app(local, port=port, client=client)
        server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:
            time.sleep(0.05)
        url = f"http://127.0.0.1:{port}/mcp"
        try:
            run_tools(url)
        finally:
            server.should_exit = True
            thread.join(timeout=10)

    # 7. Elin's voice
    tts = from_env()
    if tts is None:
        report(False, "text-to-speech", "ELEVENLABS_API_KEY is not set in .env")
    else:
        name = tts.voice_name()
        mp3 = tts.synthesize("Hi, I'm Elin. This is a short test of my voice.")
        out = ROOT / "data" / "elin_test.mp3"
        if mp3:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(mp3)
        shown = repr(name) if name else "name not checked (the key needs Voices: Read)"
        detail = f"voice {tts.voice_id} = {shown}, model {tts.model_id}"
        report(bool(mp3), "text-to-speech saved data/elin_test.mp3", detail)
    return finish()


def run_tools(url: str) -> None:
    def step(name: str, tool: str, args: dict, check) -> None:
        try:
            data = asyncio.run(call(url, tool, args))
            ok, detail = check(data)
            report(ok, name, detail)
        except Exception as exc:
            report(False, name, f"{type(exc).__name__}: {exc}")

    asyncio.run(call(url, "set_consent", {"scores": True, "notes": False}))
    step(
        "log_checkin",
        "log_checkin",
        {"mood": 3, "energy": 3, "stress": 2},
        lambda d: (d.get("aawiz") is True, d.get("speech", "")),
    )
    step(
        "get_weekly_reflection",
        "get_weekly_reflection",
        {},
        lambda d: (d.get("has_entries") is True, d.get("speech", "")),
    )
    step(
        "suggest_micro_action",
        "suggest_micro_action",
        {},
        lambda d: (bool(d.get("action")), d.get("speech", "")),
    )
    step(
        "ask_elin",
        "ask_elin",
        {"message": "Hi Elin, this is a short test."},
        lambda d: (d.get("sent") is True, d.get("speech", "")[:200]),
    )


def finish() -> int:
    ok = bool(results) and all(results)
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    os.chdir(ROOT)
    raise SystemExit(main())

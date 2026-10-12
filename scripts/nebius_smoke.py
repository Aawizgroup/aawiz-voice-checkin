"""Smoke test against Nebius Token Factory with your key. Not run in CI.

    make nebius-smoke                              the full smoke with NEBIUS_MODEL
    python scripts/nebius_smoke.py --models        only list the NVIDIA models
    python scripts/nebius_smoke.py --probe         one live tool-calling check per NVIDIA model

The smoke lists models, then runs one check-in turn and one "How was my week?" turn through the
Nebius brain with real MCP tool calls (a local MCP server on a temporary folder with synthetic
data), and prints PASS or FAIL per step and the total tokens used. The key is never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx2 as httpx  # noqa: E402
import uvicorn  # noqa: E402

from server.app import create_app  # noqa: E402
from server.config import Settings, load_dotenv  # noqa: E402
from server.demo import seed  # noqa: E402
from simulator.brain import SessionState  # noqa: E402
from simulator.brain_nebius import BASE_URL, NebiusBrain, make_client  # noqa: E402
from simulator.bridge import McpBridge  # noqa: E402

TOKEN = "smoke-token"
USER = "user_smoke"
results: list[bool] = []


def report(ok: bool, name: str, detail: str = "") -> None:
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"\n      {detail}" if detail else ""))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def nvidia_models(key: str) -> list[dict]:
    """GET /v1/models?verbose=true, filtered to NVIDIA models."""
    resp = httpx.get(
        BASE_URL + "models",
        params={"verbose": "true"},
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json().get("data", [])
    return sorted(
        (m for m in rows if "nvidia" in m["id"].lower() or "nemotron" in m["id"].lower()),
        key=lambda m: m["id"],
    )


def describe(model: dict) -> str:
    price = model.get("pricing") or {}
    features = ", ".join(model.get("supported_features") or []) or "not listed"
    return (
        f"{model['id']} | context {model.get('context_length', '?')} | "
        f"price in/out {price.get('prompt', '?')}/{price.get('completion', '?')} | "
        f"features: {features}"
    )


class LocalMcp:
    """A real MCP server on a temporary folder, seeded with synthetic data for one user."""

    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        settings = Settings(tokens={TOKEN: USER}, data_dir=Path(self.tmp.name), region="NL")
        port = free_port()
        app = create_app(settings, port=port)
        self.store = app.state.store
        self.seeded = seed(self.store, USER)
        self.server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        while not self.server.started:
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{port}/mcp"
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.tmp.cleanup()


async def probe(key: str, model_id: str, mcp: LocalMcp) -> tuple[bool, str, int]:
    """One live check: does this model call get_weekly_reflection and then answer in words?"""
    brain = NebiusBrain(McpBridge(mcp.url, TOKEN), "NL", model_id, make_client(key))
    try:
        turn = await brain.handle("How was my week?", SessionState())
    finally:
        await brain.client.close()
    tools = [c.tool for c in turn.tools]
    ok = tools == ["get_weekly_reflection"] and turn.brain == brain.label and bool(turn.reply)
    return ok, f"tools={tools} label={turn.brain!r} reply={turn.reply[:120]!r}", brain.tokens_used


async def smoke(key: str, model_id: str, mcp: LocalMcp) -> int:
    brain = NebiusBrain(McpBridge(mcp.url, TOKEN), "NL", model_id, make_client(key))
    try:
        return await _smoke_turns(brain, mcp)
    finally:
        await brain.client.close()


async def _smoke_turns(brain: NebiusBrain, mcp: LocalMcp) -> int:
    state = SessionState()

    turn = await brain.handle("My mood is 2, energy 3, stress 4, I slept 6 hours.", state)
    tools = [c.tool for c in turn.tools]
    stored = mcp.store.count(USER) == mcp.seeded + 1
    ok = "log_checkin" in tools and stored and turn.brain == brain.label
    report(
        ok, "check-in turn through Nemotron", f"tools={tools} stored={stored}\n      {turn.reply}"
    )

    turn = await brain.handle("How was my week?", state)
    tools = [c.tool for c in turn.tools]
    ok = "get_weekly_reflection" in tools and turn.brain == brain.label
    report(ok, '"How was my week?" turn through Nemotron', f"tools={tools}\n      {turn.reply}")
    return brain.tokens_used


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", action="store_true", help="only list the NVIDIA models")
    parser.add_argument("--probe", action="store_true", help="check tool calling on each one")
    args = parser.parse_args()

    load_dotenv()
    key = os.environ.get("NEBIUS_API_KEY", "").strip()
    if not key:
        print("NEBIUS_API_KEY is not set. Add it to .env (never commit it).")
        return 1

    try:
        models = nvidia_models(key)
        report(bool(models), f"list models: {len(models)} NVIDIA models on Token Factory")
    except httpx.HTTPError as exc:
        report(False, "list models", type(exc).__name__)
        return finish(0)
    for m in models:
        print("      " + describe(m))
    if args.models:
        return finish(0)

    tokens = 0
    with LocalMcp() as mcp:
        if args.probe:
            for m in models:
                try:
                    ok, detail, used = asyncio.run(probe(key, m["id"], mcp))
                except Exception as exc:
                    ok, detail, used = False, f"{type(exc).__name__}: {exc}"[:200], 0
                tokens += used
                report(ok, f"tool calling: {m['id']}", detail)
            return finish(tokens)

        model_id = os.environ.get("NEBIUS_MODEL", "").strip()
        if not model_id:
            report(False, "NEBIUS_MODEL is set in .env")
            return finish(0)
        report(any(m["id"] == model_id for m in models), f"model is an NVIDIA model: {model_id}")
        try:
            tokens = asyncio.run(smoke(key, model_id, mcp))
        except Exception as exc:
            report(False, "smoke turns", f"{type(exc).__name__}: {exc}"[:300])
    return finish(tokens)


def finish(tokens: int) -> int:
    ok = bool(results) and all(results)
    print(f"Total tokens used: {tokens}")
    print(f"RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    os.chdir(ROOT)
    for stream in (sys.stdout, sys.stderr):  # model replies may hold characters cp1252 lacks
        stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())

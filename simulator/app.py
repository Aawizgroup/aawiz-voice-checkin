"""The simulator web app: a static voice page and one JSON endpoint that runs the brain."""

from __future__ import annotations

import os
from collections import OrderedDict
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from server.config import load_dotenv
from simulator.brain import ScriptedBrain, SessionState
from simulator.bridge import McpBridge

STATIC = Path(__file__).resolve().parent / "static"
MAX_SESSIONS = 500
MAX_TEXT = 500


def _make_brain(mode: str, bridge: McpBridge, region: str, model: str):
    """Returns (brain, note). Falls back to scripted when the llm mode cannot run."""
    if mode == "llm":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return ScriptedBrain(bridge, region), "llm requested but ANTHROPIC_API_KEY is not set"
        try:
            from simulator.brain_llm import LlmBrain
        except ImportError:
            return ScriptedBrain(bridge, region), 'llm needs: pip install -e ".[llm]"'
        return LlmBrain(bridge, region, model), ""
    return ScriptedBrain(bridge, region), ""


def create_sim_app(
    mcp_url: str | None = None,
    token: str | None = None,
    mode: str | None = None,
    region: str | None = None,
    model: str | None = None,
) -> Starlette:
    load_dotenv()
    mcp_url = mcp_url or os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")
    token = token or os.environ.get("SIM_TOKEN", "")
    mode = (mode or os.environ.get("BRAIN_MODE", "scripted")).lower()
    region = (region or os.environ.get("AAWIZ_REGION", "NL")).upper()
    model = model or os.environ.get("LLM_MODEL", "claude-sonnet-5-5")

    bridge = McpBridge(mcp_url, token)
    brain, note = _make_brain(mode, bridge, region, model)
    sessions: OrderedDict[str, SessionState] = OrderedDict()

    async def index(_: Request) -> FileResponse:
        return FileResponse(STATIC / "index.html")

    async def config(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "brain": brain.mode,
                "model": model if brain.mode == "llm" else None,
                "note": note,
                "mcp_url": mcp_url,
            }
        )

    async def turn(request: Request) -> JSONResponse:
        body = await request.json()
        text = str(body.get("text", "")).strip()[:MAX_TEXT]
        session_id = str(body.get("session", "default"))[:64]
        if not text:
            return JSONResponse({"error": "empty"}, status_code=400)
        state = sessions.pop(session_id, None) or SessionState()
        sessions[session_id] = state
        while len(sessions) > MAX_SESSIONS:
            sessions.popitem(last=False)
        try:
            result = await brain.handle(text, state)
        except Exception as exc:  # the page shows a calm message instead of a stack trace
            print(f'{{"event": "turn_error", "error": "{type(exc).__name__}"}}', flush=True)
            return JSONResponse(
                {"reply": "Sorry, I couldn't reach the check-in service just now.", "tools": []},
                status_code=502,
            )
        return JSONResponse(
            {
                "reply": result.reply,
                "safety": result.safety,
                "awaiting": state.awaiting,
                "tools": [{"tool": c.tool, "ok": c.ok, "summary": c.summary} for c in result.tools],
            }
        )

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/config", config),
            Route("/api/turn", turn, methods=["POST"]),
            Route("/health", health),
            Mount("/static", StaticFiles(directory=STATIC), name="static"),
        ]
    )

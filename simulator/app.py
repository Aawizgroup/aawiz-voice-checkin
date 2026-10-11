"""The simulator web app: a static voice page and one JSON endpoint that runs the brain."""

from __future__ import annotations

import os
import secrets
from collections import OrderedDict
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from server.config import load_dotenv
from simulator.brain import ScriptedBrain, SessionState
from simulator.bridge import McpBridge
from simulator.tts import ElevenLabsTTS, from_env

STATIC = Path(__file__).resolve().parent / "static"
MAX_SESSIONS = 500
MAX_TEXT = 500
MAX_PENDING_AUDIO = 200
_FROM_ENV = object()


def _make_brain(mode: str, bridge: McpBridge, region: str, model: str, elin: bool = False):
    """Returns (brain, note). Falls back to scripted when the llm mode cannot run."""
    if mode == "llm":
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return (
                ScriptedBrain(bridge, region, elin),
                "llm requested but ANTHROPIC_API_KEY is not set",
            )
        try:
            from simulator.brain_llm import LlmBrain
        except ImportError:
            return ScriptedBrain(bridge, region, elin), 'llm needs: pip install -e ".[llm]"'
        return LlmBrain(bridge, region, model), ""
    return ScriptedBrain(bridge, region, elin), ""


def create_sim_app(
    mcp_url: str | None = None,
    token: str | None = None,
    mode: str | None = None,
    region: str | None = None,
    model: str | None = None,
    backend: str | None = None,
    tts: ElevenLabsTTS | None | object = _FROM_ENV,
) -> Starlette:
    load_dotenv()
    mcp_url = mcp_url or os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")
    token = token or os.environ.get("SIM_TOKEN", "")
    mode = (mode or os.environ.get("BRAIN_MODE", "scripted")).lower()
    region = (region or os.environ.get("AAWIZ_REGION", "NL")).upper()
    model = model or os.environ.get("LLM_MODEL", "claude-sonnet-5-5")
    backend = (backend or os.environ.get("BACKEND_MODE", "local")).lower()
    if tts is _FROM_ENV:
        tts = from_env()

    bridge = McpBridge(mcp_url, token)
    brain, note = _make_brain(mode, bridge, region, model, elin=backend == "aawiz")
    sessions: OrderedDict[str, SessionState] = OrderedDict()
    # Reply text waiting to be spoken in Elin's voice, keyed by a one-time id. Only assistant
    # replies are ever put here, so only they can reach ElevenLabs.
    pending_audio: OrderedDict[str, str] = OrderedDict()

    async def index(_: Request) -> FileResponse:
        return FileResponse(STATIC / "index.html")

    async def config(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "brain": brain.mode,
                "model": model if brain.mode == "llm" else None,
                "note": note,
                "mcp_url": mcp_url,
                "backend": backend,
                "voice": "elin" if tts else "browser",
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
        body = {
            "reply": result.reply,
            "safety": result.safety,
            "awaiting": state.awaiting,
            "tools": [{"tool": c.tool, "ok": c.ok, "summary": c.summary} for c in result.tools],
        }
        if tts:
            audio_id = secrets.token_urlsafe(12)
            pending_audio[audio_id] = result.reply
            while len(pending_audio) > MAX_PENDING_AUDIO:
                pending_audio.popitem(last=False)
            body["audio"] = f"/api/audio/{audio_id}"
        return JSONResponse(body)

    async def audio(request: Request) -> Response:
        text = pending_audio.pop(request.path_params["audio_id"], None)
        if not tts or text is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        mp3 = await run_in_threadpool(tts.synthesize, text)
        if mp3 is None:  # the page falls back to browser speech
            return JSONResponse({"error": "voice unavailable"}, status_code=503)
        return Response(mp3, media_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/config", config),
            Route("/api/turn", turn, methods=["POST"]),
            Route("/api/audio/{audio_id}", audio),
            Route("/health", health),
            Mount("/static", StaticFiles(directory=STATIC), name="static"),
        ]
    )
